"""第 ② 步：解密 gbbq -> 权息事件序列；以「最新交易日」为锚点倒序递推，
得到每个交易日的 (adj_offset, adj_scale)。

输入  ：gbbq 二进制文件 + rustdx key.rs（用于解密）；交易日历来自
         <out>/daily/<code>.csv 的 date 列（递推需要知道 n 与除权日位置）
输出  ：<out>/qfq_events.csv（事件表，按日期升序）
         <out>/factors/<code>.csv（date, adj_offset, adj_scale）

注意  ：本步必须【全量】执行——因子的递推依赖整段交易日历，gbbq 一旦更新，
         每只股票都要从 n-1 重新倒序跑到 0。
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import util
from .config import DEFAULT_GBBQ, DEFAULT_KEY_RS, DEFAULT_OUT


# ============================ gbbq 解密 ============================
def load_key(key_rs: Path) -> bytes:
    """从 rustdx 的 key.rs 提取 `pub const KEY: &[u8] = &[...];` 数组。"""
    text = key_rs.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"KEY:\s*&\[u8\]\s*=\s*&\[(.*?)\];", text, re.S)
    if not m:
        raise RuntimeError(f"在 {key_rs} 中找不到 KEY 数组")
    return bytes(int(x) for x in re.findall(r"\d+", m.group(1)))


def _u32(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


def _parse_record(enc: bytearray, KEY: bytes) -> bytearray:
    """复刻 rustdx::file::gbbq::parse，解密一条 29 字节记录的前 24 字节。"""
    pos = 0
    for _ in range(0, 24, 8):  # 0, 8, 16
        eax = _u32(KEY, 0x44)
        ebx = _u32(enc, pos)
        num = eax ^ ebx
        numold = _u32(enc, pos + 4)
        for j in range(64, 0, -4):
            ebx = (num & 0xFF0000) >> 16
            eax = _u32(KEY, ebx * 4 + 0x448)
            ebx = num >> 24
            eax = (eax + _u32(KEY, ebx * 4 + 0x48)) & 0xFFFFFFFF
            ebx = (num & 0xFF00) >> 8
            eax ^= _u32(KEY, ebx * 4 + 0x848)
            ebx = num & 0xFF
            eax = (eax + _u32(KEY, ebx * 4 + 0xC48)) & 0xFFFFFFFF
            eax ^= _u32(KEY, j)
            ebx = num
            num = numold ^ eax
            numold = ebx
        numold ^= _u32(KEY, 0)
        enc[pos:pos + 4] = numold.to_bytes(4, "little")
        enc[pos + 4:pos + 8] = num.to_bytes(4, "little")
        pos += 8
    return enc


def load_events_from_gbbq(gbbq_path: Path, key_rs: Path
                           ) -> dict[str, list[tuple[int, float, float, float, float]]]:
    """解密 gbbq，返回 6位代码 -> [(date, fh每10派, pgj配股价, sg每10送转, pg每10配股), ...]
    （仅 cat1=除权除息事件，按日期升序）。"""
    KEY = load_key(key_rs)
    raw = gbbq_path.read_bytes()
    count = _u32(raw, 0)
    out: dict[str, list] = {}
    for i in range(count):
        chunk = bytearray(raw[4 + i * 29: 4 + (i + 1) * 29])
        if len(chunk) < 29:
            break
        dec = _parse_record(chunk, KEY)
        code = dec[1:7].decode("ascii", "ignore").strip("\x00")
        if not code:
            continue
        if dec[12] != 1:  # category != 除权除息 跳过
            continue
        d = _u32(dec, 8)
        fh = struct.unpack_from("<f", dec, 13)[0]
        pgj = struct.unpack_from("<f", dec, 17)[0]
        sg = struct.unpack_from("<f", dec, 21)[0]
        pg = struct.unpack_from("<f", dec, 25)[0]
        out.setdefault(code, []).append((d, float(fh), float(pgj), float(sg), float(pg)))
    for v in out.values():
        v.sort(key=lambda r: r[0])
    return out


def write_events_csv(events: dict[str, list], out_path: Path) -> None:
    """写出权息事件表：code,date,fh,pgj,sg,pg（6 位代码，按 code,date 升序）。"""
    rows = []
    for code, evs in events.items():
        for (d, fh, pgj, sg, pg) in evs:
            rows.append((code, d, fh, pgj, sg, pg))
    rows.sort(key=lambda r: (r[0], r[1]))
    df = pd.DataFrame(rows, columns=["code", "date", "fh", "pgj", "sg", "pg"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"[events] 写出 {len(df)} 条权息记录 -> {out_path}")


# ============================ 因子递推（核心） ============================
def compute_factors(dates: np.ndarray,
                    events: list[tuple[int, float, float, float, float]]
                    ) -> tuple[np.ndarray, np.ndarray]:
    """以「最新交易日 n-1」为锚点 (offset=0, scale=1)，倒序递推得到每日因子。

    参数：
        dates  : 交易日历（升序 int64），来自 raw 日线的 date 列
        events : [(date, fh每10派, pgj配股价, sg每10送转, pg每10配股), ...]（升序）
    返回：
        (adj_offset, adj_scale) 两个等长数组；前复权价 = (raw + adj_offset)/adj_scale

    递推思路（见 qfq_pipeline 的 issue#39 累积法 + 两处修正）：
        锚点：最新日没有「未来事件」要折算，故其 offset=0, scale=1（原值不动）。
        对任意更早的 k，要与其后（含 k）发生的所有送转/配股/分红「可比」，
        必须把这些事件的影响撤销——于是从 n-1 向 0 倒序累加：
            d_acc[k] : 累积每股现金分红
            b_acc[k] : 累积「每10送转」等效比
            g_acc[k] : 累积「每10配股」等效比
            e_acc[k] : 累积配股价（随其后送转缩放后的加权值）
    """
    n = len(dates)
    if n == 0:
        return np.array([]), np.array([])

    # ---- 步骤 1：把权息事件摊平到每个交易日上（仅 cat1 除权除息）----
    #   events 字段：fh 每10派（每股=0.1*fh）、pgj 配股价(每股)、sg 每10送转、pg 每10配股
    #   这些字段在除权日 t 当天生效，故挂到 date==t 的那一行。
    div = np.zeros(n, dtype=np.float64)    # fh  : 当日现金分红（每10派）
    allot = np.zeros(n, dtype=np.float64)  # pgj: 当日配股价（每股）
    bonus = np.zeros(n, dtype=np.float64)  # sg  : 当日送转股数（每10送转）
    rights = np.zeros(n, dtype=np.float64) # pg  : 当日配股数（每10配股）
    is_ex = np.zeros(n, dtype=bool)        # 该日是否为除权除息日
    ev_map = {int(d): (fh, pgj, sg, pg) for (d, fh, pgj, sg, pg) in events}
    for k in range(n):
        e = ev_map.get(int(dates[k]))
        if e is not None:
            div[k], allot[k], bonus[k], rights[k] = e
            is_ex[k] = True

    # ---- 步骤 2：单日股本扩张比 sc ----
    #   一次除权事件可能同时含 送转(sg) 与 配股(pg)，扩张比 = (1+0.1*rights)*(1+0.1*bonus)
    #   含义：1 股老股除权后变 sc 股。非除权日 sc=1。
    sc = np.where(is_ex, (1.0 + 0.1 * rights) * (1.0 + 0.1 * bonus), 1.0)

    # ---- 步骤 3：从最新日 n-1 向更早倒序递推（算法的心脏）----
    d_acc = np.zeros(n, dtype=np.float64)
    b_acc = np.zeros(n, dtype=np.float64)
    g_acc = np.zeros(n, dtype=np.float64)
    e_acc = np.zeros(n, dtype=np.float64)
    # 最新日：它「之后」没有事件，自身事件作为起点（锚点成立：offset=0, scale=1）
    d_acc[n - 1] = div[n - 1]
    b_acc[n - 1] = bonus[n - 1]
    g_acc[n - 1] = rights[n - 1]
    e_acc[n - 1] = allot[n - 1]
    for k in range(n - 2, -1, -1):
        scn = sc[k + 1]   # k+1 当日（及其之后）的扩张比，即「对 k 的缩放系数」
        b_acc[k] = b_acc[k + 1] * scn + bonus[k + 1]   # 送转比累积
        g_acc[k] = g_acc[k + 1] * scn + rights[k + 1]  # 配股比累积
        e_acc[k] = e_acc[k + 1] * scn + allot[k + 1]   # 配股价累积
        # 现金分红累积 ——【bug2 修正】原始 issue#39 写成 d_acc[k+1]*scn，会让
        #   div[k+1]（下一日自身红利）被「它自己那次送转比」多缩一次。这里回退
        #   div[k+1]*(scn-1)，使 div[k+1] 只被「它之后更晚的事件」缩放。
        d_acc[k] = div[k] + d_acc[k + 1] * scn - div[k + 1] * (scn - 1.0)

    # ---- 步骤 4：由累加量构造仿射因子 (adj_offset, adj_scale) ----
    #   前复权价 = (raw + adj_offset) / adj_scale，单靠一个乘法因子无法表达现金分红的位移。
    den = (1.0 + 0.1 * b_acc) * (1.0 + 0.1 * g_acc)   # 总股本扩张缩放比

    # d_use：当日实际用于折算的累积现金分红。
    #   【bug1 修正】除权日当天，红利已在 raw 价里「派掉」，不应再减一次，
    #   故除权日 d_use = d_acc - div；非除权日 d_use = d_acc（全额减）。
    d_use = np.where(is_ex, d_acc - div, d_acc)

    # 偏移量 = 现金分红位移项 + 配股补偿项
    #   -0.1*d_use      : 减掉每股累积现金分红（fh 是每10派，故乘 0.1）
    #   +0.1*e_acc*g_acc: 配股折让补偿——配股以折价发行，需把折让并入前复权
    adj_offset = -0.1 * d_use + 0.1 * e_acc * g_acc
    adj_scale = den
    return adj_offset, adj_scale


# ============================ 第 ② 步入口 ============================
def run_factors(gbbq_path: Path = DEFAULT_GBBQ,
                key_rs: Path = DEFAULT_KEY_RS,
                out_dir: Path = DEFAULT_OUT,
                only: Optional[set[str]] = None) -> int:
    """解密 gbbq -> 事件表 -> 全量递推每只股票的 (adj_offset, adj_scale)。"""
    daily_dir = out_dir / "daily"
    factors_dir = out_dir / "factors"
    if not daily_dir.is_dir():
        raise RuntimeError(f"找不到 raw 日线目录: {daily_dir}，请先运行第 ① 步")
    factors_dir.mkdir(parents=True, exist_ok=True)

    # 解密事件
    events_all = load_events_from_gbbq(gbbq_path, key_rs)
    if only is not None:
        events_all = {c: e for c, e in events_all.items() if c in only}
    write_events_csv(events_all, out_dir / "qfq_events.csv")

    # 逐股票递推因子（必须全量：依赖整段交易日历）
    processed = 0
    for fp in sorted(daily_dir.glob("*.csv")):
        code = fp.stem
        if only is not None and util.six_digit(code) not in only:
            continue
        df_raw = pd.read_csv(fp)
        if df_raw.empty:
            continue
        dates = df_raw["date"].astype(np.int64).to_numpy()
        evs = events_all.get(util.six_digit(code), [])
        offset, scale = compute_factors(dates, evs)
        pd.DataFrame({
            "date": dates,
            "adj_offset": offset,
            "adj_scale": scale,
        }).to_csv(factors_dir / f"{code}.csv", index=False)
        processed += 1
        if processed % 500 == 0:
            print(f"[factors] 已处理 {processed} 只 -> {code}")

    print(f"[factors] 全量完成，共处理 {processed} 只 -> {factors_dir}")
    return processed

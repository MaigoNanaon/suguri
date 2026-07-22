#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前复权处理管线（对齐通达信 / 同花顺口径，经 600000 多锚点验证）。

============================================================
【两步管线】
============================================================
  第 1 步  raw ：解析 TDX .day 日线 + gbbq 权息事件
                  -> 输出【不复权】日线  data/daily/<code>.csv
                  -> 导出权息事件表    data/qfq_events.csv（供第 2 步独立使用）

  第 2 步  qfq ：读取 raw 日线 + 权息事件，用【修正后的 issue#39 累积法】
                  计算前复权 -> 输出   data/qfq/<code>.csv
                  每行附带【正确的复权因子】 (adj_offset, adj_scale)

  全量 all ：依次执行 raw + qfq，一次性产出沪深 5000+ 只股票的前复权历史全行情。

============================================================
【复权因子说明】（每行）
============================================================
    前复权价 = (不复权价 + adj_offset) / adj_scale
    还原     :  不复权价 = 前复权价 * adj_scale - adj_offset
    对当日 O/H/L/C 共用同一对 (adj_offset, adj_scale)。
    amount / volume 保持不复权原值（标准前复权仅调整价格）。

============================================================
【算法】issue#39 累积法 + 两处修正（已验证 600000 命中
        9.39 / 8.76 / 9.94 / 9.849 / 8.06 / 9.28）
============================================================
    bug1 除权日多减红利：除权日 k 当日 d_use = d_acc[k] - dividend[k]（仅除权日）。
        例 2017-05-25(10送3派2): qfq = 12.93 / 1.3 = 9.94（H=C 命中）。
    bug2 红利被自身送转比多缩一次：d_acc 递推由 d_acc[k+1]*sc 改为
        d_acc[k] = dividend[k] + d_acc[k+1]*sc[k+1] - dividend[k+1]*(sc[k+1]-1)，
        即把"下一日自身红利"按自身送转比回退，只让它被其后（更晚）的事件缩放。
        例 2017-01-03 收盘 = 9.393（命中 9.39）；2017-05-24 收盘 = 8.755（命中 8.76）。

    说明：本算法以"最新交易日"为锚点，最新日的前复权价 = 不复权价。
          正常 TDX 数据最新日非除权日，锚点天然成立；若最新日恰为除权日属极
          罕见边界，本脚本按通达信口径（含自身送转比）计算，如需强制最新日=原价
          可自行在 compute_qfq 末尾对最后一行做归一。

用法：
    python qfq_pipeline.py all                       # 全量（默认 Windows 路径）
    python qfq_pipeline.py raw                       # 仅第 1 步
    python qfq_pipeline.py qfq                       # 仅第 2 步
    python qfq_pipeline.py all --only 600000,000001 # 只跑指定 6 位代码
    python qfq_pipeline.py all --combined            # 额外合并成一个 parquet
    python qfq_pipeline.py self-test                 # 用 600000 验证全部锚点
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ----------------------------- 默认路径（Windows） -----------------------------
DEFAULT_KEY_RS = Path(r"g:\Projects\suguri\key.rs")
DEFAULT_GBBQ = Path(r"D:\TDX\T0002\hq_cache\gbbq")
DEFAULT_SH_DIR = Path(r"D:\TDX\vipdoc\sh\lday")
DEFAULT_SZ_DIR = Path(r"D:\TDX\vipdoc\sz\lday")
DEFAULT_OUT_DIR = Path(r"g:\Projects\suguri\data")

# .day 文件每条记录 32 字节，小端结构化布局
DAY_DTYPE = np.dtype([
    ("date", "<u4"),
    ("open", "<u4"),
    ("high", "<u4"),
    ("low", "<u4"),
    ("close", "<u4"),
    ("amount", "<f4"),
    ("volume", "<u4"),
    ("reserved", "<u4"),
])


# ============================ 板块过滤 ============================
def is_target(code: str) -> bool:
    """仅保留沪深主板 / 创业板 / 科创板（剔除 B 股、指数、债券等）。

    code 形如 "sh600000" / "sz000001"。
    """
    if len(code) < 4:
        return False
    market, num = code[:2].lower(), code[2:]
    if market == "sh":
        return num.startswith("60") or num.startswith("688")
    if market == "sz":
        return num.startswith("00") or num.startswith("30")
    return False


def six_digit(code: str) -> str:
    """从 "sh600000" / "sz000001" 取 6 位代码 "600000"（gbbq 事件表的连接键）。"""
    if len(code) > 6 and code[:2].lower() in ("sh", "sz"):
        return code[2:8]
    return code


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


def load_events_from_gbbq(gbbq_path: Path, key_rs: Path) -> dict[str, list[tuple[int, float, float, float, float]]]:
    """解密 gbbq，返回 6位代码 -> [(date, fh每10派, pgj配股价, sg每10送转, pg每10配股), ...]（仅 cat1，按日期升序）。"""
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
    """写出权息事件表：code,date,fh,pgj,sg,pg（6 位代码）。"""
    rows = []
    for code, evs in events.items():
        for (d, fh, pgj, sg, pg) in evs:
            rows.append((code, d, fh, pgj, sg, pg))
    rows.sort(key=lambda r: (r[0], r[1]))
    df = pd.DataFrame(rows, columns=["code", "date", "fh", "pgj", "sg", "pg"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"[events] 写出 {len(df)} 条权息记录 -> {out_path}")


def read_events_csv(path: Path) -> dict[str, list[tuple[int, float, float, float, float]]]:
    """从 qfq_events.csv 读回事件表。"""
    df = pd.read_csv(path, dtype={"code": str})
    out: dict[str, list] = {}
    for _, r in df.iterrows():
        out.setdefault(str(r["code"]), []).append(
            (int(r["date"]), float(r["fh"]), float(r["pgj"]), float(r["sg"]), float(r["pg"]))
        )
    for v in out.values():
        v.sort(key=lambda x: x[0])
    return out


# ============================ .day 解析 ============================
def iter_day_files(sh_dir: Path, sz_dir: Path):
    """遍历 sh/sz 的 lday 目录，对目标板块的 .day 文件逐个 yield (code, path)。"""
    for label, d in (("上海", sh_dir), ("深圳", sz_dir)):
        if not d.is_dir():
            print(f"[警告] {label}目录不存在: {d}", file=sys.stderr)
            continue
        for fp in sorted(d.glob("*.day")):
            code = fp.stem
            if not is_target(code):
                continue
            yield code, fp


def parse_day_file(path: Path):
    """解析单个 .day 文件，返回 (code, ndarray) 或 None（空/非法）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0 or size % 32 != 0:
        return None
    raw = np.fromfile(path, dtype=DAY_DTYPE)
    return raw


# ============================ 前复权核心 ============================
def compute_qfq(raw: np.ndarray, events: list[tuple[int, float, float, float, float]]) -> pd.DataFrame:
    """对单只股票计算前复权（issue#39 累积法 + 两处修正）。

    入参：
        raw    : numpy 结构化数组（DAY_DTYPE），按日期升序。
        events : [(date, fh每10派, pgj配股价, sg每10送转, pg每10配股), ...]，按日期升序。
    返回：
        DataFrame[date, open, high, low, close, volume, amount, adj_offset, adj_scale]
        （open/high/low/close 为前复权价；volume/amount 为不复权原值；
          adj_offset/adj_scale 为该行复权因子：qfq = (raw + adj_offset)/adj_scale）
    """
    n = len(raw)
    if n == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                     "volume", "amount", "adj_offset", "adj_scale"])

    dates = raw["date"].astype(np.int64)
    open_ = raw["open"].astype(np.float64) / 100.0
    high = raw["high"].astype(np.float64) / 100.0
    low = raw["low"].astype(np.float64) / 100.0
    close = raw["close"].astype(np.float64) / 100.0
    amount = raw["amount"].astype(np.float64)
    volume = raw["volume"].astype(np.int64)

    # ------------------------------------------------------------------
    # 步骤 1：把"权息事件"摊平到每个交易日上（仅 cat1 = 除权除息事件）
    #   events 中每条 (date, fh, pgj, sg, pg)：
    #     fh  = 每10股派息（现金分红），"每股"分红 = 0.1*fh
    #     pgj = 配股价（每股，元）
    #     sg  = 每10股送转股数（送股 + 转增，合并）
    #     pg  = 每10股配股数
    #   这些字段在除权日当天(t)生效，所以把事件值挂到日期 == t 的那一行。
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 步骤 2：构造"单日股本扩张比" sc
    #   一次除权事件同时可能含 送转(sg) 与 配股(pg)，其股本扩张比为
    #       sc = (1 + 0.1*rights) * (1 + 0.1*bonus)
    #   含义：1 股老股在除权后变成 sc 股（含送转+配股）。非除权日 sc = 1。
    # ------------------------------------------------------------------
    sc = np.where(is_ex, (1.0 + 0.1 * rights) * (1.0 + 0.1 * bonus), 1.0)

    # ------------------------------------------------------------------
    # 步骤 3：从"最新交易日 n-1"向更早方向递推，累积"该日之后发生的所有
    #         权益事件"对前复权的影响。
    #
    #   锚点：最新日 n-1 没有"未来事件"要折算，故其前复权价 == 不复权价。
    #   对任意更早的 k，要让 k 日价格与最新日可比，必须"撤销"k 之后（含 k）
    #   发生过的所有送转/配股/分红。于是倒序从 n-1 累加到 0。
    #
    #   四个累加量（均为"k 之后累积"，带后续事件的缩放）：
    #     d_acc[k] : 累积每股现金分红（会被其后的送转缩放）
    #     b_acc[k] : 累积"每10送转"等效比
    #     g_acc[k] : 累积"每10配股"等效比
    #     e_acc[k] : 累积配股价（随其后送转缩放后的加权值）
    # ------------------------------------------------------------------
    d_acc = np.zeros(n, dtype=np.float64)
    b_acc = np.zeros(n, dtype=np.float64)
    g_acc = np.zeros(n, dtype=np.float64)
    e_acc = np.zeros(n, dtype=np.float64)
    # 最新日：它"之后"没有事件，自身事件作为起点。
    d_acc[n - 1] = div[n - 1]
    b_acc[n - 1] = bonus[n - 1]
    g_acc[n - 1] = rights[n - 1]
    e_acc[n - 1] = allot[n - 1]
    for k in range(n - 2, -1, -1):
        scn = sc[k + 1]   # k+1 当日（及其之后）的扩张比，即"对 k 的缩放系数"
        # 送转比累积：k 之后送转 = (k+1之后送转)被 scn 缩放 + k+1 当日新送转
        b_acc[k] = b_acc[k + 1] * scn + bonus[k + 1]
        # 配股比累积：同理
        g_acc[k] = g_acc[k + 1] * scn + rights[k + 1]
        # 配股价累积：配股价随其后送转一起缩放后并入
        e_acc[k] = e_acc[k + 1] * scn + allot[k + 1]
        # 现金分红累积 ——【bug2 修正】原始 issue#39 写成 d_acc[k+1]*scn，
        #   会让 k+1 当日的分红 div[k+1] 被"它自己那次送转比"多缩一次。
        #   这里回退 div[k+1]*(scn-1)，使 div[k+1] 只被"它之后更晚的事件"缩放，
        #   不被自身送转比多缩。否则早段现金分红算多 -> 前复权价偏低
        #   （实测 2017-01-03 收盘会偏到 9.38 而非 9.39）。
        d_acc[k] = div[k] + d_acc[k + 1] * scn - div[k + 1] * (scn - 1.0)

    # ------------------------------------------------------------------
    # 步骤 4：由累加量构造"仿射因子" (adj_offset, adj_scale)
    #   最终前复权价 = (raw + adj_offset) / adj_scale
    #   即"先位移、再缩放"两步，单靠一个乘法因子无法表达现金分红的位移。
    # ------------------------------------------------------------------
    # 分母 den：把 k 之后所有送转/配股的股本扩张合并成一个总缩放比。
    #   例如 600000 到 2016-01-04 已累积 b_acc=4.3（10送4.3），故 den=1.43。
    den = (1.0 + 0.1 * b_acc) * (1.0 + 0.1 * g_acc)

    # d_use：当日实际用于折算的累积现金分红。
    #   【bug1 修正】除权日当天，红利已经在 raw 价里"派掉"了，不应再减一次，
    #   故除权日 d_use = d_acc - div（剔除自身红利）；
    #   非除权日 d_use = d_acc（全额减）。否则除权日价算低（实测 2017-05-25 会偏到 9.75）。
    d_use = np.where(is_ex, d_acc - div, d_acc)

    # 偏移量 = 现金分红位移项 + 配股补偿项
    #   -0.1*d_use     : 减掉每股累积现金分红（fh 是每10派，故乘 0.1）
    #   +0.1*e_acc*g_acc: 配股带来的"价格补偿"——配股以折价发行，需把它折让并入前复权
    adj_offset = -0.1 * d_use + 0.1 * e_acc * g_acc
    adj_scale = den                       # 缩放量

    # ------------------------------------------------------------------
    # 步骤 5：对 O/H/L/C 应用同一对仿射因子（量/额保持不复权原值）。
    # ------------------------------------------------------------------
    open_q = (open_ + adj_offset) / adj_scale
    high_q = (high + adj_offset) / adj_scale
    low_q = (low + adj_offset) / adj_scale
    close_q = (close + adj_offset) / adj_scale

    return pd.DataFrame({
        "date": dates,
        "open": open_q,
        "high": high_q,
        "low": low_q,
        "close": close_q,
        "volume": volume,
        "amount": amount,
        "adj_offset": adj_offset,
        "adj_scale": adj_scale,
    })


# ============================ 步骤实现 ============================
def step_raw(sh_dir: Path, sz_dir: Path, gbbq_path: Path, key_rs: Path,
             out_dir: Path, only: Optional[set[str]] = None) -> dict[str, list]:
    """第 1 步：解析 .day -> data/daily/<code>.csv；解密 gbbq -> data/qfq_events.csv。返回事件表。"""
    daily_dir = out_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for code, fp in iter_day_files(sh_dir, sz_dir):
        if only is not None and six_digit(code) not in only:
            continue
        raw = parse_day_file(fp)
        if raw is None or len(raw) == 0:
            continue
        df = pd.DataFrame({
            "date": raw["date"].astype(np.int64),
            "open": raw["open"].astype(np.float64) / 100.0,
            "high": raw["high"].astype(np.float64) / 100.0,
            "low": raw["low"].astype(np.float64) / 100.0,
            "close": raw["close"].astype(np.float64) / 100.0,
            "amount": raw["amount"].astype(np.float64),
            "volume": raw["volume"].astype(np.int64),
        })
        df.to_csv(daily_dir / f"{code}.csv", index=False)
        written += 1
        if written % 500 == 0:
            print(f"[raw] 已写出 {written} 只 -> {fp.name}")

    print(f"[raw] 完成，共写出 {written} 只不复权日线 -> {daily_dir}")

    events = load_events_from_gbbq(gbbq_path, key_rs)
    if only is not None:
        events = {c: e for c, e in events.items() if c in only}
    write_events_csv(events, out_dir / "qfq_events.csv")
    return events


def step_qfq(out_dir: Path, events: Optional[dict] = None,
              gbbq_path: Optional[Path] = None, key_rs: Optional[Path] = None,
              only: Optional[set[str]] = None,
              combined: bool = False) -> None:
    """第 2 步：读取 data/daily + 权息事件，计算前复权 -> data/qfq/<code>.csv。"""
    daily_dir = out_dir / "daily"
    qfq_dir = out_dir / "qfq"
    if not daily_dir.is_dir():
        raise RuntimeError(f"找不到 raw 日线目录: {daily_dir}，请先运行 raw 步骤")
    qfq_dir.mkdir(parents=True, exist_ok=True)

    # 载入事件表
    if events is None:
        ev_path = out_dir / "qfq_events.csv"
        if ev_path.is_file():
            events = read_events_csv(ev_path)
        elif gbbq_path is not None and key_rs is not None:
            events = load_events_from_gbbq(gbbq_path, key_rs)
        else:
            raise RuntimeError("未提供事件表（qfq_events.csv 与 gbbq 均缺失）")

    parquet_writer = None
    parquet_schema = None
    if combined:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
            parquet_schema = pa.schema([
                ("code", pa.string()),
                ("date", pa.int64()),
                ("open", pa.float64()),
                ("high", pa.float64()),
                ("low", pa.float64()),
                ("close", pa.float64()),
                ("volume", pa.int64()),
                ("amount", pa.float64()),
                ("adj_offset", pa.float64()),
                ("adj_scale", pa.float64()),
            ])
            parquet_writer = pq.ParquetWriter(out_dir / "qfq_all.parquet", parquet_schema)
            _pa = pa
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 无法启用合并 parquet（{exc}），仅输出分文件。", file=sys.stderr)
            parquet_writer = None

    files = sorted(daily_dir.glob("*.csv"))
    processed = 0
    for fp in files:
        code = fp.stem
        if only is not None and six_digit(code) not in only:
            continue
        df_raw = pd.read_csv(fp)
        if df_raw.empty:
            continue
        raw = np.zeros(len(df_raw), dtype=DAY_DTYPE)
        raw["date"] = df_raw["date"].astype(np.int64).to_numpy()
        raw["open"] = (df_raw["open"] * 100).round().astype(np.uint32)
        raw["high"] = (df_raw["high"] * 100).round().astype(np.uint32)
        raw["low"] = (df_raw["low"] * 100).round().astype(np.uint32)
        raw["close"] = (df_raw["close"] * 100).round().astype(np.uint32)
        raw["amount"] = df_raw["amount"].astype(np.float64).to_numpy()
        raw["volume"] = df_raw["volume"].astype(np.int64).to_numpy()

        evs = events.get(six_digit(code), [])
        df_qfq = compute_qfq(raw, evs)
        df_qfq.insert(0, "code", code)
        # 分文件输出（去掉 code 列，文件名已含代码）
        df_qfq.drop(columns=["code"]).to_csv(qfq_dir / f"{code}.csv", index=False)

        if parquet_writer is not None:
            parquet_writer.write_table(_pa.Table.from_pandas(df_qfq, schema=parquet_schema))

        processed += 1
        if processed % 500 == 0:
            print(f"[qfq] 已处理 {processed} 只 -> {code}")

    if parquet_writer is not None:
        parquet_writer.close()
        print(f"[qfq] 合并 parquet -> {out_dir / 'qfq_all.parquet'}")

    print(f"[qfq] 完成，共处理 {processed} 只 -> {qfq_dir}")


# ============================ 自测 ============================
EXPECTED_600000 = {
    20160104: ("OHLC", 9.28, 9.28, 8.77, 8.94),
    20170103: ("C", 9.39, None, None, None),
    20170524: ("C", 8.76, None, None, None),
    20170525: ("HC", 9.94, 9.94, None, None),
    20170526: ("C", 9.849, None, None, None),
    20181030: ("C", 8.06, None, None, None),
}


def self_test(sh_dir: Path, sz_dir: Path, gbbq_path: Path, key_rs: Path) -> int:
    """用 600000 真实数据验证全部锚点（容差 0.015）。"""
    # 解析 600000 的日线
    target = None
    for code, fp in iter_day_files(sh_dir, sz_dir):
        if six_digit(code) == "600000":
            target = (code, fp)
            break
    if target is None:
        print("[self-test] 未找到 600000 的 .day 文件", file=sys.stderr)
        return 1
    code, fp = target
    raw = parse_day_file(fp)
    events = load_events_from_gbbq(gbbq_path, key_rs).get("600000", [])
    df = compute_qfq(raw, events)
    df_idx = {int(d): r for d, r in zip(df["date"], df.itertuples(index=False))}

    ok = True
    for dt, (kind, *vals) in EXPECTED_600000.items():
        row = df_idx.get(dt)
        if row is None:
            print(f"  [FAIL] {dt}: 无交易日")
            ok = False
            continue

        def chk(name, got, exp):
            nonlocal ok
            if exp is None:
                return
            if abs(got - exp) > 0.015:
                print(f"  [FAIL] {dt} {name}: 得到 {got:.4f}, 期望 ~{exp}")
                ok = False
            else:
                print(f"  [ OK ] {dt} {name}: {got:.4f} ~ {exp}")

        if kind in ("OHLC",):
            chk("O", row.open, vals[0]); chk("H", row.high, vals[1])
            chk("L", row.low, vals[2]); chk("C", row.close, vals[3])
        elif kind == "C":
            chk("C", row.close, vals[0])
        elif kind == "HC":
            chk("H", row.high, vals[0]); chk("C", row.close, vals[1])
    print("[self-test]", "全部锚点命中 OK" if ok else "存在偏差 FAIL")
    return 0 if ok else 1


# ============================ CLI ============================
def _parse_only(s: Optional[str]) -> Optional[set[str]]:
    if not s:
        return None
    return {x.strip() for x in s.split(",") if x.strip()}


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        description="前复权处理管线：解析 day+gbbq -> 不复权日线 -> 前复权全行情")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--sh-dir", type=Path, default=DEFAULT_SH_DIR, help="上海 lday 目录")
        sp.add_argument("--sz-dir", type=Path, default=DEFAULT_SZ_DIR, help="深圳 lday 目录")
        sp.add_argument("--gbbq", type=Path, default=DEFAULT_GBBQ, help="gbbq 文件路径")
        sp.add_argument("--key-rs", type=Path, default=DEFAULT_KEY_RS, help="rustdx key.rs 路径")
        sp.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="输出根目录")
        sp.add_argument("--only", type=str, default="", help="仅处理指定 6 位代码，逗号分隔")

    sp_raw = sub.add_parser("raw", help="第 1 步：解析 day+gbbq 为不复权日线")
    add_common(sp_raw)

    sp_qfq = sub.add_parser("qfq", help="第 2 步：计算前复权")
    add_common(sp_qfq)
    sp_qfq.add_argument("--combined", action="store_true", help="额外合并成一个 parquet")

    sp_all = sub.add_parser("all", help="raw + qfq 连跑（默认）")
    add_common(sp_all)
    sp_all.add_argument("--combined", action="store_true", help="额外合并成一个 parquet")

    sp_st = sub.add_parser("self-test", help="用 600000 验证锚点")
    add_common(sp_st)

    args = p.parse_args(argv)
    only = _parse_only(getattr(args, "only", ""))

    if args.cmd == "self-test":
        return self_test(args.sh_dir, args.sz_dir, args.gbbq, args.key_rs)

    if args.cmd == "raw":
        step_raw(args.sh_dir, args.sz_dir, args.gbbq, args.key_rs, args.out, only)
        return 0

    if args.cmd == "qfq":
        step_qfq(args.out, events=None, gbbq_path=args.gbbq,
                 key_rs=args.key_rs, only=only, combined=args.combined)
        return 0

    if args.cmd == "all":
        events = step_raw(args.sh_dir, args.sz_dir, args.gbbq, args.key_rs, args.out, only)
        step_qfq(args.out, events=events, only=only, combined=args.combined)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

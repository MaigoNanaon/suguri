"""qfq_wf 命令行入口。

子命令：
    raw       第 ① 步：解析 lday -> 不复权日线 CSV（--incremental 增量）
    factors   第 ② 步：解密 gbbq -> 事件 + 全量递推每日因子
    qfq       第 ③ 步：raw + 因子 -> 前复权 OHLC CSV（全量）
    all       ① + ② + ③ 连跑（默认 raw 增量；可用 --full 强制全量）
    self-test 用 600000 真实数据校验全部锚点（容差 0.015）

示例：
    python -m qfq_wf all
    python -m qfq_wf raw --incremental
    python -m qfq_wf all --only 600009,600000
    python -m qfq_wf self-test
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import config, util
from .step1_raw import parse_day_files
from .step2_factors import compute_factors, load_events_from_gbbq, run_factors
from .step3_qfq import build_qfq
from .step4_resample import resample_all
from .step5_drawline import drawline_all

# 600000 在通达信前复权下的已知真值（kind 决定校验字段）
EXPECTED_600000 = {
    20160104: ("OHLC", 9.28, 9.28, 8.77, 8.94),
    20170103: ("C", 9.39, None, None, None),
    20170524: ("C", 8.76, None, None, None),
    20170525: ("HC", 9.94, 9.94, None, None),
    20170526: ("C", 9.849, None, None, None),
    20181030: ("C", 8.06, None, None, None),
}


def _parse_only(s: Optional[str]) -> Optional[set[str]]:
    if not s:
        return None
    return {util.six_digit(x.strip()) for x in s.split(",") if x.strip()}


def _self_test(sh_dir: Path, sz_dir: Path, gbbq: Path, key_rs: Path) -> int:
    """用 600000 真实数据验证：因子递推 + 仿射变换全部锚点命中。"""
    target = None
    for d in (sh_dir, sz_dir):
        if not d.is_dir():
            continue
        cand = d / "sh600000.day"
        if cand.is_file():
            target = cand
            break
    if target is None:
        print("[self-test] 未找到 sh600000.day", file=sys.stderr)
        return 1

    from .step1_raw import parse_day_file
    raw = parse_day_file(target)
    if raw is None or len(raw) == 0:
        print("[self-test] 600000 日线为空", file=sys.stderr)
        return 1
    dates = raw["date"].astype(np.int64)
    events = load_events_from_gbbq(gbbq, key_rs).get("600000", [])
    offset, scale = compute_factors(dates, events)

    open_ = raw["open"].astype(np.float64) / 100.0
    high = raw["high"].astype(np.float64) / 100.0
    low = raw["low"].astype(np.float64) / 100.0
    close = raw["close"].astype(np.float64) / 100.0
    df = pd.DataFrame({
        "date": dates,
        "open": (open_ + offset) / scale,
        "high": (high + offset) / scale,
        "low": (low + offset) / scale,
        "close": (close + offset) / scale,
    })
    idx = {int(r.date): r for r in df.itertuples(index=False)}

    ok = True
    for dt, (kind, *vals) in EXPECTED_600000.items():
        row = idx.get(dt)
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

        if kind == "OHLC":
            chk("O", row.open, vals[0]); chk("H", row.high, vals[1])
            chk("L", row.low, vals[2]); chk("C", row.close, vals[3])
        elif kind == "C":
            chk("C", row.close, vals[0])
        elif kind == "HC":
            chk("H", row.high, vals[0]); chk("C", row.close, vals[1])
    print("[self-test]", "全部锚点命中 OK" if ok else "存在偏差 FAIL")
    return 0 if ok else 1


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="前复权独立工作流（① raw ② factors ③ qfq）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_io(sp):
        sp.add_argument("--sh-dir", type=Path, default=config.DEFAULT_SH_DIR, help="上海 lday 目录")
        sp.add_argument("--sz-dir", type=Path, default=config.DEFAULT_SZ_DIR, help="深圳 lday 目录")
        sp.add_argument("--gbbq", type=Path, default=config.DEFAULT_GBBQ, help="gbbq 文件路径")
        sp.add_argument("--key-rs", type=Path, default=config.DEFAULT_KEY_RS, help="rustdx key.rs 路径")
        sp.add_argument("--out", type=Path, default=config.DEFAULT_OUT, help="输出根目录")
        sp.add_argument("--only", type=str, default="", help="仅处理指定 6 位代码，逗号分隔")

    sp_raw = sub.add_parser("raw", help="第 ① 步：解析 lday 为不复权日线")
    add_io(sp_raw)
    sp_raw.add_argument("--incremental", action="store_true", help="增量更新（仅重解析变更的 .day）")

    sp_fac = sub.add_parser("factors", help="第 ② 步：解密 gbbq + 全量递推因子")
    add_io(sp_fac)

    sp_qfq = sub.add_parser("qfq", help="第 ③ 步：raw + 因子 -> 前复权")
    add_io(sp_qfq)

    sp_res = sub.add_parser("resample", help="第 ④ 步：前复权日线 -> 周/月/45日/季/年 K 线")
    add_io(sp_res)

    sp_dl = sub.add_parser("drawline", help="第 ⑤ 步：前复权日线 -> 划线集（跨年价 + 上市首开）")
    add_io(sp_dl)

    sp_all = sub.add_parser("all", help="① + ② + ③ 连跑")
    add_io(sp_all)
    sp_all.add_argument("--full", action="store_true", help="raw 强制全量（默认增量）")

    sp_st = sub.add_parser("self-test", help="用 600000 校验锚点")
    add_io(sp_st)

    args = p.parse_args(argv)
    only = _parse_only(getattr(args, "only", ""))

    if args.cmd == "self-test":
        return _self_test(args.sh_dir, args.sz_dir, args.gbbq, args.key_rs)

    if args.cmd == "raw":
        parse_day_files(args.sh_dir, args.sz_dir, args.out, only,
                        incremental=getattr(args, "incremental", False))
        return 0

    if args.cmd == "factors":
        run_factors(args.gbbq, args.key_rs, args.out, only)
        return 0

    if args.cmd == "qfq":
        build_qfq(args.out, only)
        return 0

    if args.cmd == "resample":
        resample_all(args.out, only)
        return 0

    if args.cmd == "drawline":
        drawline_all(args.out, only)
        return 0

    if args.cmd == "all":
        parse_day_files(args.sh_dir, args.sz_dir, args.out, only,
                        incremental=not getattr(args, "full", False))
        run_factors(args.gbbq, args.key_rs, args.out, only)
        build_qfq(args.out, only)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

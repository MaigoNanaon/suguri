# -*- coding: utf-8 -*-
"""
解析通达信 sh/sz 的 .day 日线数据，按股票分别存为 CSV 到 data/daily 目录。

只保留以下板块（其余剔除，如 B 股、指数、债券等）:
    - 沪市主板     sh 6xxxxxx  (代码以 "60" 开头, 600~605)
    - 科创板       sh 688xxx  (代码以 "688" 开头)
    - 深市主板     sz 0xxxxx   (代码以 "00" 开头, 000~003, 含原中小板 002)
    - 创业板       sz 30xxxx   (代码以 "30" 开头, 300~301)

.day 文件格式（小端，每条 32 字节）:
    date(uint32 YYYYMMDD), open/high/low/close(uint32 ×100),
    amount(float32 元), volume(uint32 股), reserved(uint32)

用法:
    python parse_daily.py
    python parse_daily.py --sh-dir D:\\TDX\\vipdoc\\sh\\lday --sz-dir D:\\TDX\\vipdoc\\sz\\lday --out data\\daily
"""

import argparse
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

RECORD_SIZE = 32

# numpy 结构化 dtype，与 .day 二进制布局一一对应（小端）
_DAY_DTYPE = np.dtype([
    ("date", "<u4"),
    ("open", "<u4"),
    ("high", "<u4"),
    ("low", "<u4"),
    ("close", "<u4"),
    ("amount", "<f4"),
    ("volume", "<u4"),
    ("reserved", "<u4"),
])

# 默认数据目录（Windows 绝对路径）
DEFAULT_SH_DIR = r"D:\TDX\vipdoc\sh\lday"
DEFAULT_SZ_DIR = r"D:\TDX\vipdoc\sz\lday"
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daily")


def is_target(code: str) -> bool:
    """判断股票代码是否属于目标板块（沪深主板/创业板/科创板）。

    Args:
        code: 形如 "sh600000" / "sz300001" 的代码。

    Returns:
        True 表示需要保留。
    """
    if len(code) < 4:
        return False
    market, num = code[:2].lower(), code[2:]
    if market == "sh":
        # 沪市主板 60xxxx，科创板 688xxx
        return num.startswith("60") or num.startswith("688")
    if market == "sz":
        # 深市主板 00xxxx（含 002 中小板），创业板 30xxxx
        return num.startswith("00") or num.startswith("30")
    return False


def parse_day_file(filepath: str) -> Optional[pd.DataFrame]:
    """解析单个 .day 文件，返回含 code/date/open/high/low/close/amount/volume 的 DataFrame。

    Returns:
        正常返回 DataFrame；文件为空或大小非法返回 None。
    """
    file_size = os.path.getsize(filepath)
    if file_size == 0 or file_size % RECORD_SIZE != 0:
        return None

    raw = np.fromfile(filepath, dtype=_DAY_DTYPE)
    code = os.path.splitext(os.path.basename(filepath))[0]

    df = pd.DataFrame({
        "code": code,
        "date": pd.to_datetime(raw["date"].astype(str), format="%Y%m%d"),
        "open": raw["open"] / 100.0,
        "high": raw["high"] / 100.0,
        "low": raw["low"] / 100.0,
        "close": raw["close"] / 100.0,
        "amount": raw["amount"].astype(np.float64),   # 成交额(元)
        "volume": raw["volume"].astype(np.int64),      # 成交量(股)
    })
    return df


def process_dir(src_dir: str, out_dir: str, counter: list) -> None:
    """遍历源目录，筛选目标股票并逐只写出 CSV。"""
    for fname in sorted(os.listdir(src_dir)):
        if not fname.lower().endswith(".day"):
            continue
        code = os.path.splitext(fname)[0]
        if not is_target(code):
            continue  # 板块过滤：B股/指数/债券等跳过

        fp = os.path.join(src_dir, fname)
        try:
            df = parse_day_file(fp)
        except Exception as exc:  # noqa: BLE001
            print(f"[跳过] {fp}: {exc}", file=sys.stderr)
            continue

        if df is None or df.empty:
            continue

        out_path = os.path.join(out_dir, f"{code}.csv")
        df.to_csv(out_path, index=False, encoding="utf-8-sig")

        counter[0] += 1
        if counter[0] % 200 == 0:
            print(f"已写出 {counter[0]} 只 -> {out_path}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="解析 sh/sz .day 日线，仅保留沪深主板/创业板/科创板，存为 CSV")
    parser.add_argument("--sh-dir", default=DEFAULT_SH_DIR, help=f"上海 lday 目录 (默认: {DEFAULT_SH_DIR})")
    parser.add_argument("--sz-dir", default=DEFAULT_SZ_DIR, help=f"深圳 lday 目录 (默认: {DEFAULT_SZ_DIR})")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help=f"输出目录 (默认: {DEFAULT_OUT_DIR})")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    counter = [0]

    for label, d in (("上海", args.sh_dir), ("深圳", args.sz_dir)):
        if not os.path.isdir(d):
            print(f"[警告] {label}目录不存在: {d}", file=sys.stderr)
            continue
        print(f"开始解析{label}目录: {d}")
        process_dir(d, args.out, counter)

    print(f"\n完成。共输出 {counter[0]} 只股票的 CSV 到: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

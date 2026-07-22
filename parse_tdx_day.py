# -*- coding: utf-8 -*-
"""
解析通达信 (TDX) .day 股票日线数据文件到 pandas DataFrame。

.day 文件格式说明（小端，每条记录固定 32 字节）:
    偏移  类型      字段        说明
    0     uint32   date        交易日期，格式 YYYYMMDD
    4     uint32   open         开盘价 (实际价格 * 100)
    8     uint32   high         最高价 (实际价格 * 100)
    12    uint32   low          最低价 (实际价格 * 100)
    16    uint32   close        收盘价 (实际价格 * 100)
    20    float32  amount       成交额 (单位: 元)
    24    uint32   volume       成交量 (单位: 股)
    28    uint32   reserved     保留字段 (通常为 0)

用法示例:
    # 解析单个文件
    python parse_tdx_day.py --file D:\\TDX\\vipdoc\\sh\\lday\\sh600000.day

    # 解析整个目录 (默认目录即为 D:\\TDX\\vipdoc\\sh\\lday\\)
    python parse_tdx_day.py --dir D:\\TDX\\vipdoc\\sh\\lday\\

    # 解析目录并把合并结果导出为 parquet
    python parse_tdx_day.py --dir D:\\TDX\\vipdoc\\sh\\lday\\ --out all_sh.parquet
"""

import argparse
import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

# 每条记录的字节数
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


def parse_day_file(filepath: str) -> pd.DataFrame:
    """解析单个 .day 文件，返回 DataFrame。

    Args:
        filepath: .day 文件的完整路径。

    Returns:
        包含 code, date, open, high, low, close, amount, volume 列的 DataFrame。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件大小不是 32 字节的整数倍（格式异常）。
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")

    file_size = os.path.getsize(filepath)
    if file_size == 0:
        # 空文件返回空 DataFrame，避免中断批量处理
        return _empty_frame()
    if file_size % RECORD_SIZE != 0:
        raise ValueError(
            f"文件大小 {file_size} 不是 {RECORD_SIZE} 的整数倍，可能不是有效的 .day 文件: {filepath}"
        )

    # 使用 numpy 一次性读入，效率高
    raw = np.fromfile(filepath, dtype=_DAY_DTYPE)

    # 股票代码取文件名（去扩展名），如 sh600000
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


def _empty_frame() -> pd.DataFrame:
    """返回具有统一列结构的空 DataFrame。"""
    return pd.DataFrame(
        columns=["code", "date", "open", "high", "low", "close", "amount", "volume"]
    )


def parse_day_dir(dirpath: str, pattern: str = ".day") -> pd.DataFrame:
    """解析目录下所有 .day 文件并合并为一个 DataFrame。

    Args:
        dirpath: 存放 .day 文件的目录。
        pattern: 文件扩展名过滤，默认 ".day"。

    Returns:
        合并后的 DataFrame（含所有股票）。
    """
    if not os.path.isdir(dirpath):
        raise NotADirectoryError(f"目录不存在: {dirpath}")

    files = [
        os.path.join(dirpath, f)
        for f in os.listdir(dirpath)
        if f.lower().endswith(pattern.lower())
    ]
    files.sort()

    if not files:
        print(f"[警告] 目录中未找到 {pattern} 文件: {dirpath}", file=sys.stderr)
        return _empty_frame()

    frames = []
    total = len(files)
    for idx, fp in enumerate(files, start=1):
        try:
            df = parse_day_file(fp)
            if not df.empty:
                frames.append(df)
        except (ValueError, FileNotFoundError) as exc:
            # 单个文件出错不影响整体，打印后继续
            print(f"[跳过] {fp}: {exc}", file=sys.stderr)
            continue

        if idx % 200 == 0 or idx == total:
            print(f"进度: {idx}/{total}")

    if not frames:
        return _empty_frame()

    result = pd.concat(frames, ignore_index=True)
    return result


def save_dataframe(df: pd.DataFrame, out_path: str) -> None:
    """按扩展名把 DataFrame 保存为 csv / parquet / pickle。"""
    ext = os.path.splitext(out_path)[1].lower()
    try:
        if ext == ".csv":
            df.to_csv(out_path, index=False, encoding="utf-8-sig")
        elif ext == ".parquet":
            df.to_parquet(out_path, index=False)
        elif ext in (".pkl", ".pickle"):
            df.to_pickle(out_path)
        else:
            raise ValueError(f"不支持的输出格式: {ext} (可用: .csv / .parquet / .pkl)")
        print(f"已保存: {out_path}  (共 {len(df)} 行)")
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 保存失败 {out_path}: {exc}", file=sys.stderr)
        raise


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="解析通达信 .day 日线数据到 pandas DataFrame"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", help="解析单个 .day 文件的路径")
    group.add_argument(
        "--dir",
        default=r"D:\TDX\vipdoc\sh\lday",
        help=r"解析整个目录 (默认: D:\TDX\vipdoc\sh\lday)",
    )
    parser.add_argument(
        "--out",
        help="可选，输出文件路径 (.csv / .parquet / .pkl)。不指定则按 --out-dir 自动保存。",
    )
    parser.add_argument(
        "--out-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="默认输出目录 (不指定 --out 时，结果的存放目录)。默认为脚本所在目录。",
    )
    parser.add_argument(
        "--fmt",
        default="csv",
        choices=["csv", "parquet", "pkl"],
        help="未指定 --out 时的输出格式 (默认: csv，无需额外依赖)。",
    )
    args = parser.parse_args(argv)

    try:
        if args.file:
            df = parse_day_file(args.file)
        else:
            df = parse_day_dir(args.dir)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1

    # 打印概览信息
    print("\n===== 解析结果概览 =====")
    print(f"总记录数: {len(df)}")
    if not df.empty:
        print(f"股票数量: {df['code'].nunique()}")
        print(f"日期范围: {df['date'].min().date()}  ~  {df['date'].max().date()}")
        print("\n前 5 行:")
        print(df.head().to_string(index=False))
        print("\n数据类型:")
        print(df.dtypes.to_string())

    # 决定输出路径: 显式 --out 优先；否则按 --out-dir + 默认文件名保存
    if args.out:
        out_path = args.out
    else:
        os.makedirs(args.out_dir, exist_ok=True)
        if args.file:
            stem = os.path.splitext(os.path.basename(args.file))[0]
        else:
            # 取目录末级名作为文件名前缀，避免与源目录同名
            stem = os.path.basename(args.dir.rstrip(os.sep)) or "tdx"
        out_path = os.path.join(args.out_dir, f"{stem}_day.{args.fmt}")

    save_dataframe(df, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

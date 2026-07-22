"""第 ③ 步：由 raw 行情 + 两个因子，推算每日前复权 OHLC，写出 CSV。

输入  ：<out>/daily/<code>.csv（raw 行情，价格已是元）
         <out>/factors/<code>.csv（date, adj_offset, adj_scale）
输出  ：<out>/qfq/<code>.csv（date, open, high, low, close, volume, amount, adj_offset, adj_scale）

公式  ：前复权价 = (raw + adj_offset) / adj_scale
         对 O/H/L/C 共用同一对因子；volume/amount 保持不复权原值。

注意  ：本步必须【全量】执行——gbbq 更新后因子整体会变，前复权价需重算。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import util
from .config import DEFAULT_OUT

# 输出列顺序
QFQ_COLS = ["date", "open", "high", "low", "close", "volume", "amount",
             "adj_offset", "adj_scale"]


def build_qfq_for_code(raw_csv: Path, factors_csv: Path) -> Optional[pd.DataFrame]:
    """对单只股票：raw + 因子 -> 前复权 DataFrame。失败/空返回 None。"""
    df_raw = pd.read_csv(raw_csv)
    if df_raw.empty:
        return None
    df_fac = pd.read_csv(factors_csv)
    if df_fac.empty:
        return None

    # 以 date 为键合并；保证价格与因子行对齐
    df = df_raw.merge(df_fac, on="date", how="inner").sort_values("date")
    if df.empty:
        return None

    # raw 价格（已是元）+ 因子，做仿射变换
    open_ = df["open"].to_numpy(dtype=np.float64)
    high = df["high"].to_numpy(dtype=np.float64)
    low = df["low"].to_numpy(dtype=np.float64)
    close = df["close"].to_numpy(dtype=np.float64)
    offset = df["adj_offset"].to_numpy(dtype=np.float64)
    scale = df["adj_scale"].to_numpy(dtype=np.float64)

    out = pd.DataFrame({
        "date": df["date"].astype(np.int64),
        "open": (open_ + offset) / scale,
        "high": (high + offset) / scale,
        "low": (low + offset) / scale,
        "close": (close + offset) / scale,
        "volume": df["volume"].astype(np.int64),
        "amount": df["amount"].astype(np.float64),
        "adj_offset": offset,
        "adj_scale": scale,
    })
    return out[QFQ_COLS]


def build_qfq(out_dir: Path = DEFAULT_OUT,
               only: Optional[set[str]] = None) -> int:
    """全量生成前复权 CSV。返回处理文件数。"""
    daily_dir = out_dir / "daily"
    factors_dir = out_dir / "factors"
    qfq_dir = out_dir / "qfq"
    if not daily_dir.is_dir():
        raise RuntimeError(f"找不到 raw 日线目录: {daily_dir}")
    if not factors_dir.is_dir():
        raise RuntimeError(f"找不到 factors 目录: {factors_dir}，请先运行第 ② 步")
    qfq_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    for fp in sorted(factors_dir.glob("*.csv")):
        code = fp.stem
        if only is not None and util.six_digit(code) not in only:
            continue
        raw_csv = daily_dir / f"{code}.csv"
        if not raw_csv.is_file():
            print(f"[警告] 缺 raw 行情: {raw_csv}", file=sys.stderr)
            continue
        df = build_qfq_for_code(raw_csv, fp)
        if df is None or df.empty:
            continue
        df.to_csv(qfq_dir / f"{code}.csv", index=False)
        processed += 1
        if processed % 500 == 0:
            print(f"[qfq] 已处理 {processed} 只 -> {code}")

    print(f"[qfq] 全量完成，共处理 {processed} 只 -> {qfq_dir}")
    return processed

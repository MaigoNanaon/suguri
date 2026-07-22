"""第 ④ 步：对前复权日线做 resample，生成周/月/45日/季/年 K 线。

输入  ：<out>/qfq/<code>.csv（前复权日线，date 为 YYYYMMDD 整型）
输出  ：<out>/resample/<code>_<tag>.csv，每文件 4 根数值列
         tag ∈ {W 周, M 月, 45D 45日, Q 季, Y 年}
         列：date, open, high, low, close（仅 OHLC，不含量/额）

周期定义：
  - 周/月/季/年 : 按【自然日历】分组（pandas Period）
                   周以周一为起点（W-MON，覆盖 Mon..Sun）；
                   月/季/年末为各自期间边界。
  - 45日        : 按【交易天数】连续切片——第 1..45 个交易日为第 1 根，
                   第 46..90 为第 2 根，依此类推（末尾不足 45 日也成一根）。

每根 K 线的 OHLC：
  open  = 该期间【首】个交易日的开盘
  close = 该期间【末】个交易日的收盘
  high  = 该期间全部交易日的最高价
  low   = 该期间全部交易日的最低价
  date  = 该期间【末】个交易日的实际日期（真实交易日，便于与日线对齐）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import util
from .config import DEFAULT_OUT

# 自然日历周期对应的 pandas Period 频率
#   周 W-SUN：以【周一为起点、周日为界】（覆盖 Mon..Sun）。
#               注意 pandas 锚点字母表示「周的结束日」，故 Mon..Sun 周须用 W-SUN；
#               若误用 W-MON 会变成 Tue..Mon，跨周日边界错并到同一根。
#   月 M / 季 Q / 年 Y：均以期间末日为界
PERIOD_FREQ = {"W": "W-SUN", "M": "M", "Q": "Q", "Y": "Y"}

# 全部要生成的周期标签（顺序即输出顺序）
TAGS = ["W", "M", "Q", "Y", "45D"]

_TAG_NAME = {"W": "周", "M": "月", "Q": "季", "Y": "年", "45D": "45日"}


def _period_key(dates: pd.Series, tag: str) -> pd.Series:
    """返回每个交易日所属周期的【分组键】。"""
    if tag == "45D":
        # 按交易天数连续切片：行号 // 45
        return pd.Series(np.arange(len(dates)), index=dates.index) // 45
    dt = pd.to_datetime(dates, format="%Y%m%d")
    return dt.dt.to_period(PERIOD_FREQ[tag])


def resample_qfq(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    """对单只股票的前复权日线 df 做单个周期的 resample。

    参数 df：至少含 date(int YYYYMMDD), open, high, low, close。
    返回    ：date, open, high, low, close（按时间升序）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    df = df.sort_values("date").reset_index(drop=True)

    key = _period_key(df["date"], tag)
    g = df.groupby(key, sort=True)
    out = g.agg(
        date=("date", "last"),    # 期间末交易日实际日期
        open=("open", "first"),   # 期间首交易日开盘
        high=("high", "max"),     # 期间最高
        low=("low", "min"),       # 期间最低
        close=("close", "last"),  # 期间末交易日收盘
    ).reset_index(drop=True)
    return out[["date", "open", "high", "low", "close"]]


def resample_all(out_dir: Path = DEFAULT_OUT,
                 only: Optional[set[str]] = None) -> int:
    """对 qfq 目录下全部股票生成 5 种周期 K 线。返回处理股票数。"""
    qfq_dir = out_dir / "qfq"
    res_dir = out_dir / "resample"
    if not qfq_dir.is_dir():
        raise RuntimeError(f"找不到 qfq 目录: {qfq_dir}，请先运行第 ③ 步")
    res_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    for fp in sorted(qfq_dir.glob("*.csv")):
        code = fp.stem
        if only is not None and util.six_digit(code) not in only:
            continue
        df = pd.read_csv(fp)
        if df.empty:
            continue
        for tag in TAGS:
            r = resample_qfq(df, tag)
            r.to_csv(res_dir / f"{code}_{tag}.csv", index=False)
        processed += 1
        if processed % 500 == 0:
            print(f"[resample] 已处理 {processed} 只 -> {code}")

    print(f"[resample] 完成，共处理 {processed} 只 -> {res_dir} "
          f"（周期：{', '.join(_TAG_NAME[t]+'('+t+')' for t in TAGS)}）")
    return processed

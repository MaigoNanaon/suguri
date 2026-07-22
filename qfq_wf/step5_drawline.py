"""第 ⑤ 步：由前复权日线生成【划线集】。

概念定义
--------
跨年价：指某年【最后一个交易日收盘价】和【来年（次年）第一个交易日开盘价】
        两者中的较小值。仅当相邻两年 Y 与 Y+1 都有交易数据时才成立
        （若 Y+1 整年停牌/无数据，则该年不产生跨年价）。
划线集：一个集合，包含该股票历史以来的所有跨年价，
        以及【上市第一个交易日的开盘价】。

输入  ：<out>/qfq/<code>.csv（前复权日线，date 为 YYYYMMDD 整型）
输出  ：<out>/drawline/<code>.csv
        列：kind, year, date_a, price_a, date_b, price_b, price
          kind   : ipo（上市首开）| cross_year（跨年价）
          year   : ipo -> 上市年份；cross_year -> 前一年 Y
          date_a : ipo -> 上市首日；   cross_year -> 年 Y 最后交易日
          price_a: ipo -> 首日开盘；   cross_year -> 年 Y 最后交易日收盘
          date_b : cross_year -> 年 Y+1 首个交易日（ipo 留空）
          price_b: cross_year -> 年 Y+1 首日开盘（ipo 留空）
          price  : 划线价（ipo -> 首日开盘；cross_year -> min(price_a, price_b)）
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import util
from .config import DEFAULT_OUT

# 输出列顺序
DRAWLINE_COLS = ["kind", "year", "date_a", "price_a",
                 "date_b", "price_b", "price"]


def build_drawline(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票的前复权日线 df 生成划线集。

    参数 df：至少含 date(int YYYYMMDD), open, close。
    返回    ：DRAWLINE_COLS 列，按 year 升序（首行为上市首开）。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=DRAWLINE_COLS)

    df = df.sort_values("date").reset_index(drop=True)
    year = (df["date"].astype(np.int64) // 10000).to_numpy()

    rows: list[dict] = []

    # ① 上市第一个交易日开盘价
    first = df.iloc[0]
    rows.append({
        "kind": "ipo",
        "year": int(year[0]),
        "date_a": int(first["date"]),
        "price_a": float(first["open"]),
        "date_b": "",
        "price_b": "",
        "price": float(first["open"]),
    })

    # ② 逐年跨年价：需相邻两年 Y 与 Y+1 均有数据
    g = df.groupby(year, sort=True)
    year_first = g.first()   # 各年首个交易日（date 最小，因已排序）
    year_last = g.last()     # 各年末个交易日
    year_set = set(int(y) for y in year_first.index)

    for y in sorted(year_set):
        if (y + 1) not in year_set:
            continue
        last_close = float(year_last.loc[y, "close"])
        next_open = float(year_first.loc[y + 1, "open"])
        rows.append({
            "kind": "cross_year",
            "year": int(y),
            "date_a": int(year_last.loc[y, "date"]),
            "price_a": last_close,
            "date_b": int(year_first.loc[y + 1, "date"]),
            "price_b": next_open,
            "price": min(last_close, next_open),
        })

    return pd.DataFrame(rows, columns=DRAWLINE_COLS)


def drawline_all(out_dir: Path = DEFAULT_OUT,
                 only: Optional[set[str]] = None) -> int:
    """对 qfq 目录下全部股票生成划线集。返回处理股票数。"""
    qfq_dir = out_dir / "qfq"
    dl_dir = out_dir / "drawline"
    if not qfq_dir.is_dir():
        raise RuntimeError(f"找不到 qfq 目录: {qfq_dir}，请先运行第 ③ 步")
    dl_dir.mkdir(parents=True, exist_ok=True)

    processed = 0
    for fp in sorted(qfq_dir.glob("*.csv")):
        code = fp.stem
        if only is not None and util.six_digit(code) not in only:
            continue
        df = pd.read_csv(fp)
        if df.empty:
            continue
        dl = build_drawline(df)
        dl.to_csv(dl_dir / f"{code}.csv", index=False)
        processed += 1
        if processed % 500 == 0:
            print(f"[drawline] 已处理 {processed} 只 -> {code}")

    print(f"[drawline] 完成，共处理 {processed} 只 -> {dl_dir}")
    return processed

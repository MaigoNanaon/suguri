"""第 ① 步：从 sh/sz 的 lday 目录解析【不复权】日线，写出 CSV。

输入  ：上海/深圳 lday 文件夹路径（--sh-dir / --sz-dir）
输出  ：<out>/daily/<code>.csv，每只股票一个文件
增量  ：--incremental 时，仅重新解析「源 .day 比已有 CSV 更新」或「尚不存在」的文件；
         其余文件沿用旧 CSV，不再读取 .day，从而把每日增量更新的 I/O 降到最低。

说明  ：价格字段已折算为元（/100），与后续因子推算处于同一价格尺度；
        volume/amount 保持 TDX 原始单位（股、元）不变。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from . import util
from .config import DEFAULT_SH_DIR, DEFAULT_SZ_DIR, DEFAULT_OUT

# .day 单条记录固定 32 字节，小端结构化布局
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


def parse_day_file(path: Path) -> Optional[np.ndarray]:
    """解析单个 .day 文件为结构化数组；空文件/非法长度返回 None。"""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0 or size % 32 != 0:
        return None
    return np.fromfile(path, dtype=DAY_DTYPE)


def _iter_day_files(sh_dir: Path, sz_dir: Path):
    """按板块遍历 lday 目录，逐文件 yield (code, path)。"""
    for label, d in (("上海", sh_dir), ("深圳", sz_dir)):
        if not d.is_dir():
            print(f"[警告] {label} lday 目录不存在: {d}", file=sys.stderr)
            continue
        for fp in sorted(d.glob("*.day")):
            yield fp.stem, fp


def raw_array_to_df(raw: np.ndarray) -> pd.DataFrame:
    """结构化数组 -> 不复权日线 DataFrame（价格已转成元，/100）。"""
    return pd.DataFrame({
        "date": raw["date"].astype(np.int64),
        "open": raw["open"].astype(np.float64) / 100.0,
        "high": raw["high"].astype(np.float64) / 100.0,
        "low": raw["low"].astype(np.float64) / 100.0,
        "close": raw["close"].astype(np.float64) / 100.0,
        "amount": raw["amount"].astype(np.float64),
        "volume": raw["volume"].astype(np.int64),
    })


def parse_day_files(sh_dir: Path = DEFAULT_SH_DIR,
                    sz_dir: Path = DEFAULT_SZ_DIR,
                    out_dir: Path = DEFAULT_OUT,
                    only: Optional[Iterable[str]] = None,
                    incremental: bool = False) -> int:
    """解析 lday -> <out_dir>/daily/<code>.csv。返回写出/更新文件数。

    参数：
        incremental : True 时启用增量更新——跳过「CSV 已存在且未过期」的文件。
                     False 时全量重解析所有目标文件。
    """
    daily_dir = out_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    only_set = {util.six_digit(c) for c in only} if only else None
    written = 0
    for code, fp in _iter_day_files(sh_dir, sz_dir):
        if not util.is_target(code):
            continue
        if only_set is not None and util.six_digit(code) not in only_set:
            continue

        dst = daily_dir / f"{code}.csv"
        # ---- 增量判断：CSV 已存在且源 .day 未更新 -> 直接跳过 ----
        if incremental and dst.is_file():
            try:
                if dst.stat().st_mtime >= fp.stat().st_mtime:
                    continue
            except OSError:
                pass  # mtime 读不到就保守地重新解析

        raw = parse_day_file(fp)
        if raw is None or len(raw) == 0:
            continue
        raw_array_to_df(raw).to_csv(dst, index=False)
        written += 1
        if written % 500 == 0:
            print(f"[raw] 已写出 {written} 只 -> {fp.name}")

    mode = "增量" if incremental else "全量"
    print(f"[raw] {mode}完成，共写出/更新 {written} 只 -> {daily_dir}")
    return written

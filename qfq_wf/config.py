"""默认路径配置（Windows 环境，可被 CLI 覆盖）。

路径说明：
  ROOT            : 本仓库根目录（qfq_wf 的父目录）
  DEFAULT_KEY_RS  : rustdx 的 gbbq 解密密钥 key.rs
  DEFAULT_GBBQ    : 通达信 gbbq 二进制文件（权息数据）
  DEFAULT_SH_DIR  : 上海 lday 日线文件夹
  DEFAULT_SZ_DIR  : 深圳 lday 日线文件夹
  DEFAULT_OUT     : 输出根目录；其内部再分 daily/ factors/ qfq/ 三个子目录
"""
from pathlib import Path

# 本文件位于 <root>/qfq_wf/config.py，故父目录即仓库根
ROOT = Path(__file__).resolve().parent.parent

# gbbq 解密密钥（已抽取到仓库根目录，脱离 rustdx 依赖）
DEFAULT_KEY_RS = ROOT / "key.rs"

# TDX 数据源目录
DEFAULT_GBBQ = Path(r"D:\TDX\T0002\hq_cache\gbbq")
DEFAULT_SH_DIR = Path(r"D:\TDX\vipdoc\sh\lday")
DEFAULT_SZ_DIR = Path(r"D:\TDX\vipdoc\sz\lday")

# 输出根目录（raw/factors/qfq 各自子目录均在此之下）
DEFAULT_OUT = ROOT / "data"

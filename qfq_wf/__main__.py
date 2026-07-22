"""支持 `python -m qfq_wf` 直接运行。"""
from .cli import main
import sys

if __name__ == "__main__":
    raise SystemExit(main())

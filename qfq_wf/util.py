"""公共小工具。"""


def six_digit(code: str) -> str:
    """从 "sh600000" / "sz000001" 取 6 位代码 "600000"（gbbq 事件表的连接键）。

    若传入的已是 6 位代码则原样返回。
    """
    if len(code) > 6 and code[:2].lower() in ("sh", "sz"):
        return code[2:8]
    return code


def is_target(code: str) -> bool:
    """仅处理沪深主板 / 创业板 / 科创板（剔除 B 股、指数、债券等）。

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

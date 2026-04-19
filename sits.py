import math
from typing import Any


SITS_PRECISION = 3
SITS_EPS = 1e-9


def to_sits(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        raw = value.strip().replace(",", ".")
        if not raw:
            return 0.0
        number = float(raw)
    else:
        number = float(value)

    if not math.isfinite(number):
        raise ValueError("Sits value must be finite")
    return round(number, SITS_PRECISION)


def normalize_sits(value: Any) -> int | float:
    amount = to_sits(value)
    rounded_int = round(amount)
    if abs(amount - rounded_int) <= SITS_EPS:
        return int(rounded_int)
    return amount


def parse_sits(text: str) -> int | float:
    if text is None:
        raise ValueError("Empty sits value")
    raw = text.strip().replace(",", ".")
    if not raw:
        raise ValueError("Empty sits value")
    return normalize_sits(raw)


def format_sits(value: Any) -> str:
    normalized = normalize_sits(value)
    if isinstance(normalized, int):
        return str(normalized)
    return f"{normalized:.{SITS_PRECISION}f}".rstrip("0").rstrip(".")


def sit_word(value: Any) -> str:
    normalized = normalize_sits(value)
    if not isinstance(normalized, int):
        return "сита"
    n = abs(normalized)
    if n % 10 == 1 and n % 100 != 11:
        return "сит"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "сита"
    return "сит"

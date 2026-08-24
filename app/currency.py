"""Currency helpers.

Steam's market API takes a numeric currency id (Valve's ECurrencyCode).
USD is 1, ILS is 35. If a price ever looks wrong (e.g. the returned symbol
isn't a shekel), double check against Valve's ECurrencyCode enum and update
CURRENCY_IDS below.
"""
import re

CURRENCY_IDS = {
    "USD": 1,
    "ILS": 35,
}

SYMBOLS = {
    "USD": "$",
    "ILS": "₪",
}


def currency_id(code: str) -> int:
    code = code.upper()
    if code not in CURRENCY_IDS:
        raise ValueError(f"Unsupported currency: {code}")
    return CURRENCY_IDS[code]


_NUM_RE = re.compile(r"[\d.,]+")


def parse_price(raw: str | None) -> float | None:
    """Parse a Steam-formatted price string (e.g. '$12.34', '12,34€', '₪ 45.90') into a float."""
    if not raw:
        return None
    match = _NUM_RE.search(raw)
    if not match:
        return None
    num = match.group(0)
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        tail = num.split(",")[-1]
        if len(tail) == 2:
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


def format_price(value: float | None, currency: str) -> str:
    if value is None:
        return "-"
    symbol = SYMBOLS.get(currency.upper(), "")
    return f"{symbol}{value:,.2f}"

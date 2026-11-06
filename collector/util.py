"""Unit conversion, time and hashing helpers (DECISIONS.md D4)."""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def iso_ms(s):
    """ISO-8601 string (any fractional precision, 'Z' or offset) -> UTC epoch ms."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if s[-1] in "Zz":
        s = s[:-1] + "+00:00"
    if "." in s:
        head, rest = s.split(".", 1)
        i = 0
        while i < len(rest) and rest[i].isdigit():
            i += 1
        s = f"{head}.{(rest[:i] + '000000')[:6]}{rest[i:]}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - EPOCH) // timedelta(milliseconds=1)


def num(x):
    """Decimal string / number / {'value': ...} amount -> float (None if missing)."""
    if isinstance(x, dict):
        x = x.get("value")
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def cents(x):
    """Dollar amount -> cents as float. Sub-cent prices are kept."""
    v = num(x)
    return None if v is None else round(v * 100, 6)


def flip(c):
    """Complementary price in cents (YES ask = 100 - NO bid)."""
    return None if c is None else round(100 - c, 6)


def js(x):
    return None if x is None else json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def b(x):
    return None if x is None else bool(x)


def s(x):
    return None if x is None or x == "" else str(x)


def utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")

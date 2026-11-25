"""A-Share Trading Calendar and SSE Holiday Schedule with CST Timezone Support."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Sequence, Union

DateLike = Union[date, datetime, str]

# China Standard Time (UTC+8)
CST = timezone(timedelta(hours=8))

def get_cst_now() -> datetime:
    """Return current datetime in China Standard Time (UTC+8)."""
    return datetime.now(CST)

def get_cst_today() -> date:
    """Return today's date in China Standard Time (UTC+8)."""
    return get_cst_now().date()

# SSE Official Holidays (2024 - 2027)
OFFICIAL_HOLIDAYS = {
    # 2024
    ("2024-01-01", "2024-01-01"),
    ("2024-02-09", "2024-02-17"),
    ("2024-04-04", "2024-04-06"),
    ("2024-05-01", "2024-05-05"),
    ("2024-06-08", "2024-06-10"),
    ("2024-09-15", "2024-09-17"),
    ("2024-10-01", "2024-10-07"),
    # 2025
    ("2025-01-01", "2025-01-01"),
    ("2025-01-28", "2025-02-04"),
    ("2025-04-04", "2025-04-06"),
    ("2025-05-01", "2025-05-05"),
    ("2025-05-31", "2025-06-02"),
    ("2025-10-01", "2025-10-08"),
    # 2026
    ("2026-01-01", "2026-01-03"),
    ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"),
    ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
    # 2027
    ("2027-01-01", "2027-01-03"),
    ("2027-02-06", "2027-02-14"),
    ("2027-04-03", "2027-04-05"),
    ("2027-05-01", "2027-05-05"),
    ("2027-06-09", "2027-06-11"),
    ("2027-09-15", "2027-09-17"),
    ("2027-10-01", "2027-10-07"),
}

def coerce_date(val: DateLike) -> date:
    """Coerce string, datetime or date to datetime.date."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        val = val.strip()
        if "T" in val:
            val = val.split("T")[0]
        elif " " in val:
            val = val.split(" ")[0]
        return date.fromisoformat(val)
    raise ValueError(f"Cannot coerce {val} to date")

def is_official_holiday(d: date) -> bool:
    """Check if date falls within SSE official holiday closures."""
    iso = d.isoformat()
    for start, end in OFFICIAL_HOLIDAYS:
        if start <= iso <= end:
            return True
    return False

def is_trading_day(val: DateLike) -> bool:
    """Check if date is a valid A-share trading day (weekday and non-holiday)."""
    d = coerce_date(val)
    if d.weekday() >= 5:
        return False
    if is_official_holiday(d):
        return False
    return True

def get_latest_trading_day(as_of: DateLike | None = None) -> date:
    """Get the most recent trading day on or before as_of in China Time."""
    cur = coerce_date(as_of) if as_of else get_cst_today()
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur

def get_previous_trading_day(as_of: DateLike) -> date:
    """Get the trading day strictly before as_of."""
    cur = coerce_date(as_of) - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur

def get_recent_trading_days(count: int, end_date: DateLike | None = None) -> list[date]:
    """Get `count` recent trading days up to `end_date`, newest first."""
    cur = coerce_date(end_date) if end_date else get_cst_today()
    results = []
    while len(results) < count:
        if is_trading_day(cur):
            results.append(cur)
        cur -= timedelta(days=1)
    return results

"""Pure planning rules for HR Admin "Generate Weekly Off".

No database or web-framework imports live here so the rules can be unit
tested in isolation. ``attendance.py`` gathers the facts (which dates are
``Not Marked`` and eligible, how many Weekly Offs already exist) and calls
these helpers to suggest dates and to validate what HR Admin submits.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

MAX_WEEKLY_OFFS_PER_MONTH = 4

# Tie-break when two weekdays are equally common: Sunday, then Saturday, then
# Monday..Friday (date.weekday(): Monday=0 ... Sunday=6).
_TIE_PREFERENCE = {6: 0, 5: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6}


def rank_weekdays(candidates: Iterable[date], configured_weekdays: Iterable[int] = ()) -> list[int]:
    """Weekdays (0=Monday) ordered from most to least suitable for Weekly Off.

    Only weekdays that actually occur among the ``Not Marked`` candidate dates
    are returned. A weekday the company configured as a weekly off wins over an
    unconfigured one; otherwise the most frequent weekday wins.
    """
    configured = set(configured_weekdays or ())
    counts: dict[int, int] = {}
    for item in candidates:
        counts[item.weekday()] = counts.get(item.weekday(), 0) + 1
    return sorted(
        counts,
        key=lambda day: (0 if day in configured else 1, -counts[day], _TIE_PREFERENCE[day]),
    )


def suggest_weekly_off_dates(
    candidates: Iterable[date],
    remaining: int,
    configured_weekdays: Iterable[int] = (),
) -> list[date]:
    """Pick up to ``remaining`` (never more than four) dates, oldest first.

    Dates of the best-ranked weekday are used first, then the next weekday, and
    so on, so the result follows the employee's own recurring pattern.
    """
    limit = max(0, min(int(remaining), MAX_WEEKLY_OFFS_PER_MONTH))
    unique = sorted(set(candidates))
    if not limit or not unique:
        return []
    chosen: list[date] = []
    for weekday in rank_weekdays(unique, configured_weekdays):
        for item in unique:
            if item.weekday() == weekday and len(chosen) < limit:
                chosen.append(item)
        if len(chosen) >= limit:
            break
    return sorted(chosen)


def validate_weekly_off_selection(
    selected: list[str],
    eligible: Iterable[str],
    existing_weekly_offs: int,
    monthly_limit: int = MAX_WEEKLY_OFFS_PER_MONTH,
) -> list[str]:
    """Return a list of human-readable problems; empty means the selection is valid."""
    limit = min(int(monthly_limit), MAX_WEEKLY_OFFS_PER_MONTH)
    eligible_set = set(eligible)
    problems: list[str] = []
    if not selected:
        return ["Select at least one Weekly Off date."]
    if len(set(selected)) != len(selected):
        problems.append("Duplicate dates were submitted.")
    remaining = max(0, limit - int(existing_weekly_offs))
    if remaining == 0:
        problems.append(f"The monthly Weekly Off limit of {limit} has already been reached.")
    elif len(set(selected)) > remaining:
        problems.append(
            f"Only {remaining} more Weekly Off date(s) can be added this month "
            f"(limit {limit}, already used {existing_weekly_offs})."
        )
    invalid = sorted(day for day in set(selected) if day not in eligible_set)
    if invalid:
        problems.append(
            "These dates are not eligible Not Marked dates (already marked, holiday, leave, "
            "before joining, locked or out of month): " + ", ".join(invalid) + "."
        )
    return problems

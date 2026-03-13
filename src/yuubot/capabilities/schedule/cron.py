"""Cron expression validation and long-cycle detection."""

from apscheduler.triggers.cron import CronTrigger


def validate_cron(cron_expr: str) -> None:
    """Validate a 5-field cron expression. Raises ValueError if invalid."""
    CronTrigger.from_crontab(cron_expr)


def _expand_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand a single cron field into a set of integer values."""
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            range_part, step_s = part.split("/", 1)
            step = int(step_s)
            if range_part == "*":
                start, end = lo, hi
            elif "-" in range_part:
                a, b = range_part.split("-", 1)
                start, end = int(a), int(b)
            else:
                start, end = int(range_part), hi
            values.update(range(start, end + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            values.update(range(int(a), int(b) + 1))
        elif part == "*":
            values.update(range(lo, hi + 1))
        else:
            values.add(int(part))
    return values


def is_long_cycle(cron_expr: str) -> bool:
    """Return True if the cron pattern has a repeating period > 1 month.

    A cron expression whose month field covers all 12 months has a period
    of at most 1 month (or shorter).  If the month field is restricted,
    the pattern repeats on a longer-than-monthly basis.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {cron_expr}")
    month_field = parts[3]
    months = _expand_field(month_field, 1, 12)
    return len(months) < 12

"""``apple-mail cal`` command handlers (read-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..envelope import list_payload
from ..errors import validation
from ..timeparse import local_tz, to_epoch
from . import store


def cmd_calendars(args) -> dict:
    return list_payload(store.list_calendars())


def _window(args) -> tuple[float, float]:
    """Resolve a start/end epoch window from --start/--end or --days."""
    if args.start or args.end:
        if not (args.start and args.end):
            raise validation("provide both --start and --end, or use --days")
        return to_epoch(args.start), to_epoch(args.end)
    days = args.days if args.days is not None else 7
    now = datetime.now(tz=local_tz())
    end = now + timedelta(days=days)
    return now.timestamp(), end.timestamp()


def cmd_list(args) -> dict:
    start, end = _window(args)
    cals = args.calendar or None
    items = store.list_events(start, end, calendars=cals, limit=args.limit)
    return list_payload(items)


def cmd_get(args) -> dict:
    return store.get_event(args.id)

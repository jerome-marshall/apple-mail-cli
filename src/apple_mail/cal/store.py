"""Read the local Calendar.app store directly (FDA-only, no EventKit/TCC).

macOS keeps Calendar data in a SQLite database at
``~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb``.
Reading it needs only Full Disk Access - the same grant mail already needs - so
``cal`` works without the EventKit TCC prompt, which a plain (adhoc-signed, no
usage-description) CLI cannot pass anyway.

The big win is the ``OccurrenceCache`` table: macOS pre-expands recurring events
into concrete occurrences (a ~+/-2 year horizon around now), so we get correct
recurring instances in a window without implementing RRULE expansion ourselves -
exactly what EventKit would have done internally.

Dates are CoreFoundation absolute time (seconds since 2001-01-01 UTC); add
``_CF_EPOCH`` to convert to Unix. The DB is usually in WAL mode, so we copy the
db + -wal + -shm to a temp dir and read the copy (sees latest data, never touches
the live store).
"""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from ..errors import AppleMailError, full_disk_access, not_found, platform_unsupported, validation
from ..timeparse import unix_to_local_iso

_CF_EPOCH = 978307200

CAL_DIR = Path.home() / "Library" / "Group Containers" / "group.com.apple.calendar"
CAL_DB = CAL_DIR / "Calendar.sqlitedb"
CAL_WAL = CAL_DIR / "Calendar.sqlitedb-wal"
CAL_SHM = CAL_DIR / "Calendar.sqlitedb-shm"

# CalendarItem.status -> EKEventStatus-like names.
_ESTATUS = {0: "none", 1: "confirmed", 2: "tentative", 3: "canceled"}
# Participant enums (Apple internal; best-effort, raw value passed through if unknown).
_ROLE = {0: "unknown", 1: "required", 2: "optional", 3: "chair", 4: "nonParticipant"}
_PSTATUS = {0: "unknown", 1: "pending", 2: "accepted", 3: "declined", 4: "tentative", 5: "delegated"}
_PTYPE = {0: "unknown", 1: "person", 2: "room", 3: "resource", 4: "group"}
_STORE_TYPE = {0: "local", 1: "exchange", 2: "caldav", 3: "mobileme", 4: "subscribed", 5: "birthday"}


def _is_macos() -> bool:
    return sys.platform == "darwin"


def cf_iso(ts) -> str | None:
    if ts is None:
        return None
    return unix_to_local_iso(float(ts) + _CF_EPOCH)


def _preview(text, n: int = 200) -> str | None:
    if not text:
        return None
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


@contextlib.contextmanager
def open_calendar_db():
    if not _is_macos():
        raise platform_unsupported()
    try:
        exists = CAL_DB.exists()
    except PermissionError:
        raise full_disk_access() from None
    if not exists:
        raise AppleMailError(
            "CALENDAR_STORE_NOT_FOUND",
            "Calendar.sqlitedb not found. Add the work account's Calendar via "
            "System Settings > Internet Accounts and let it sync.",
        )

    tmp = None
    try:
        try:
            has_wal = CAL_WAL.exists() and CAL_WAL.stat().st_size > 0
        except PermissionError:
            raise full_disk_access() from None

        if has_wal:
            tmp = Path(tempfile.mkdtemp(prefix="apple-mail-cal-"))
            try:
                shutil.copy2(CAL_DB, tmp / "c.sqlitedb")
                shutil.copy2(CAL_WAL, tmp / "c.sqlitedb-wal")
                if CAL_SHM.exists():
                    shutil.copy2(CAL_SHM, tmp / "c.sqlitedb-shm")
            except PermissionError:
                raise full_disk_access() from None
            conn = sqlite3.connect(str(tmp / "c.sqlitedb"), timeout=5)
        else:
            try:
                conn = sqlite3.connect(f"file:{CAL_DB}?mode=ro&immutable=1", uri=True, timeout=5)
            except sqlite3.OperationalError as exc:
                if "unable to open" in str(exc).lower():
                    raise full_disk_access() from None
                raise AppleMailError("CALENDAR_STORE_ERROR", str(exc)) from None

        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# -- participants --------------------------------------------------------------


def _participant_dict(r: sqlite3.Row) -> dict:
    return {
        "name": r["display_name"] or None,
        "email": r["email"] or None,
        "role": _ROLE.get(r["role"], r["role"]),
        "status": _PSTATUS.get(r["status"], r["status"]),
        "type": _PTYPE.get(r["type"], r["type"]),
        "isSelf": bool(r["is_self"]),
    }


_PARTICIPANT_SELECT = (
    "SELECT p.ROWID AS rid, p.owner_id AS owner_id, p.email AS email, p.role AS role, "
    "p.status AS status, p.type AS type, p.is_self AS is_self, i.display_name AS display_name "
    "FROM Participant p LEFT JOIN Identity i ON i.ROWID = p.identity_id"
)


def _fetch_participants_by_rowid(conn, rowids) -> dict:
    rowids = [r for r in dict.fromkeys(rowids) if r]
    if not rowids:
        return {}
    ph = ",".join("?" for _ in rowids)
    rows = conn.execute(f"{_PARTICIPANT_SELECT} WHERE p.ROWID IN ({ph})", rowids).fetchall()
    return {r["rid"]: _participant_dict(r) for r in rows}


def _attendee_counts(conn, owner_ids) -> dict:
    owner_ids = [r for r in dict.fromkeys(owner_ids) if r]
    if not owner_ids:
        return {}
    ph = ",".join("?" for _ in owner_ids)
    rows = conn.execute(
        f"SELECT owner_id, COUNT(*) AS c FROM Participant WHERE owner_id IN ({ph}) GROUP BY owner_id",
        owner_ids,
    ).fetchall()
    return {r["owner_id"]: r["c"] for r in rows}


def _attendees(conn, owner_id) -> list[dict]:
    rows = conn.execute(
        f"{_PARTICIPANT_SELECT} WHERE p.owner_id = ? ORDER BY p.ROWID", [owner_id]
    ).fetchall()
    return [_participant_dict(r) for r in rows]


# -- calendars -----------------------------------------------------------------


def list_calendars() -> list[dict]:
    with open_calendar_db() as conn:
        rows = conn.execute(
            "SELECT cal.ROWID AS id, cal.title AS title, cal.type AS type, cal.UUID AS uuid, "
            "st.name AS account, st.type AS store_type "
            "FROM Calendar cal LEFT JOIN Store st ON st.ROWID = cal.store_id "
            "ORDER BY cal.display_order, cal.title"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "type": r["type"],
                "account": r["account"],
                "storeType": _STORE_TYPE.get(r["store_type"], r["store_type"]),
                "uuid": r["uuid"],
            }
            for r in rows
        ]


def _resolve_calendar_ids(conn, names) -> list[int]:
    ids: list[int] = []
    rows = conn.execute("SELECT ROWID AS id, title FROM Calendar").fetchall()
    for n in names:
        if str(n).isdigit():
            ids.append(int(n))
            continue
        needle = str(n).lower()
        for r in rows:
            if r["title"] and needle in r["title"].lower():
                ids.append(r["id"])
    return list(dict.fromkeys(ids))


# -- events --------------------------------------------------------------------


def _event_item(r: sqlite3.Row, organizer, attendee_count: int) -> dict:
    return {
        "id": r["id"],
        "uid": r["unique_identifier"],
        "title": r["summary"],
        "start": cf_iso(r["start_cf"]),
        "end": cf_iso(r["end_cf"]),
        "allDay": bool(r["all_day"]),
        "location": r["loc_title"] or r["loc_addr"] or None,
        "calendar": r["cal_title"],
        "organizer": organizer,
        "attendeeCount": attendee_count,
        "isRecurring": bool(r["has_recurrences"]),
        "status": _ESTATUS.get(r["status"], r["status"]),
        "url": r["url"] or None,
        "notesPreview": _preview(r["description"]),
    }


def list_events(start_unix: float, end_unix: float, calendars=None, limit=None) -> list[dict]:
    with open_calendar_db() as conn:
        # Window-overlap (like EventKit's predicate): an occurrence intersects the
        # window if it starts before the window ends and ends after it begins.
        # GROUP BY (event, start) collapses the per-day rows that the cache stores
        # for multi-day events, while keeping distinct recurring instances.
        params: list = [end_unix - _CF_EPOCH, start_unix - _CF_EPOCH]
        cal_filter = ""
        if calendars:
            ids = _resolve_calendar_ids(conn, calendars)
            if not ids:
                raise not_found(f"no calendar matching {calendars!r}")
            ph = ",".join("?" for _ in ids)
            cal_filter = f"AND ci.calendar_id IN ({ph})"
            params.extend(ids)

        sql = f"""
            SELECT oc.event_id AS id,
                   COALESCE(oc.occurrence_start_date, oc.occurrence_date) AS start_cf,
                   oc.occurrence_end_date AS end_cf,
                   ci.summary AS summary, ci.all_day AS all_day, ci.description AS description,
                   ci.url AS url, ci.status AS status, ci.has_recurrences AS has_recurrences,
                   ci.unique_identifier AS unique_identifier, ci.organizer_id AS organizer_id,
                   cal.title AS cal_title, loc.title AS loc_title, loc.address AS loc_addr
            FROM OccurrenceCache oc
            JOIN CalendarItem ci ON ci.ROWID = oc.event_id
            JOIN Calendar cal ON cal.ROWID = ci.calendar_id
            LEFT JOIN Location loc ON loc.ROWID = ci.location_id
            WHERE COALESCE(oc.occurrence_start_date, oc.occurrence_date) <= ?
              AND (oc.occurrence_end_date IS NULL OR oc.occurrence_end_date >= ?)
              {cal_filter}
            GROUP BY oc.event_id, start_cf
            ORDER BY start_cf
        """
        rows = conn.execute(sql, params).fetchall()
        if limit:
            rows = rows[:limit]
        organizers = _fetch_participants_by_rowid(conn, [r["organizer_id"] for r in rows])
        counts = _attendee_counts(conn, [r["id"] for r in rows])
        return [
            _event_item(r, organizers.get(r["organizer_id"]), counts.get(r["id"], 0)) for r in rows
        ]


def _recurrence(conn, owner_id) -> dict | None:
    row = conn.execute(
        "SELECT frequency, interval, count, end_date, week_start FROM Recurrence WHERE owner_id = ? LIMIT 1",
        [owner_id],
    ).fetchone()
    if row is None:
        return None
    return {
        "frequency": row["frequency"],
        "interval": row["interval"],
        "count": row["count"],
        "until": cf_iso(row["end_date"]),
    }


def get_event(item_id) -> dict:
    try:
        rid = int(item_id)
    except (TypeError, ValueError):
        raise validation(f"event id must be an integer, got {item_id!r}") from None

    with open_calendar_db() as conn:
        r = conn.execute(
            "SELECT ci.ROWID AS id, ci.summary AS summary, ci.start_date AS start_date, "
            "ci.end_date AS end_date, ci.all_day AS all_day, ci.description AS description, "
            "ci.url AS url, ci.status AS status, ci.has_recurrences AS has_recurrences, "
            "ci.unique_identifier AS unique_identifier, ci.organizer_id AS organizer_id, "
            "ci.last_modified AS last_modified, cal.title AS cal_title, "
            "loc.title AS loc_title, loc.address AS loc_addr "
            "FROM CalendarItem ci JOIN Calendar cal ON cal.ROWID = ci.calendar_id "
            "LEFT JOIN Location loc ON loc.ROWID = ci.location_id WHERE ci.ROWID = ?",
            [rid],
        ).fetchone()
        if r is None:
            raise not_found(f"no event with id {rid}")
        organizer = None
        if r["organizer_id"]:
            organizer = _fetch_participants_by_rowid(conn, [r["organizer_id"]]).get(r["organizer_id"])
        return {
            "id": r["id"],
            "uid": r["unique_identifier"],
            "title": r["summary"],
            "start": cf_iso(r["start_date"]),
            "end": cf_iso(r["end_date"]),
            "allDay": bool(r["all_day"]),
            "location": r["loc_title"] or r["loc_addr"] or None,
            "calendar": r["cal_title"],
            "organizer": organizer,
            "attendees": _attendees(conn, rid),
            "notes": r["description"] or None,
            "isRecurring": bool(r["has_recurrences"]),
            "recurrence": _recurrence(conn, rid) if r["has_recurrences"] else None,
            "status": _ESTATUS.get(r["status"], r["status"]),
            "url": r["url"] or None,
            "lastModified": cf_iso(r["last_modified"]),
            "note": (
                "For recurring events, start/end are the series master; use cal list "
                "for per-occurrence times."
                if r["has_recurrences"]
                else None
            ),
        }


def health() -> dict:
    try:
        with open_calendar_db() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM CalendarItem").fetchone()["c"]
        return {"ok": True, "store": str(CAL_DB), "itemCount": n}
    except AppleMailError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}

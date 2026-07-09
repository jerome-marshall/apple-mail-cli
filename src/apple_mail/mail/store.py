"""Locate and open the Apple Mail store, and introspect its SQLite schema.

The on-disk layout is ``~/Library/Mail/V<N>/`` where ``<N>`` bumps with major
macOS releases (V10/V11/... on recent systems). The searchable metadata lives in
``V<N>/MailData/Envelope Index`` (a SQLite database). Full message bodies live in
``.emlx`` files scattered under the per-account ``.mbox`` folders.

Because the schema varies by macOS version, nothing here hardcodes column lists.
We open the DB read-only/immutable (so we never lock Mail's live database) and
introspect tables/columns at runtime via ``MailSchema``.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import AppleMailError, full_disk_access, mail_store_not_found, platform_unsupported

MAIL_ROOT = Path.home() / "Library" / "Mail"


def is_macos() -> bool:
    return sys.platform == "darwin"


def locate_version_dir() -> Path:
    """Return the highest-numbered ``~/Library/Mail/V<N>`` directory."""
    if not is_macos():
        raise platform_unsupported()
    try:
        candidates = [
            p
            for p in MAIL_ROOT.iterdir()
            if p.is_dir() and p.name.startswith("V") and p.name[1:].isdigit()
        ]
    except PermissionError:
        raise full_disk_access() from None
    except FileNotFoundError:
        raise mail_store_not_found("~/Library/Mail does not exist") from None

    if not candidates:
        raise mail_store_not_found("no V<N> directory under ~/Library/Mail")
    return sorted(candidates, key=lambda p: int(p.name[1:]))[-1]


def envelope_index_path(version_dir: Path) -> Path:
    return version_dir / "MailData" / "Envelope Index"


def open_index() -> tuple[sqlite3.Connection, Path]:
    """Open the Envelope Index read-only. Returns (connection, version_dir)."""
    version_dir = locate_version_dir()
    db_path = envelope_index_path(version_dir)

    try:
        exists = db_path.exists()
    except PermissionError:
        raise full_disk_access() from None
    if not exists:
        raise mail_store_not_found(f"{db_path} not found")

    # immutable=1 => no locking/WAL handshake against Mail's live DB.
    uri = f"file:{db_path}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "unable to open" in msg or "authorization" in msg or "permission" in msg:
            raise full_disk_access() from None
        raise AppleMailError("MAIL_STORE_ERROR", f"could not open Envelope Index: {exc}") from None
    conn.row_factory = sqlite3.Row
    return conn, version_dir


# --- Schema introspection -----------------------------------------------------


@dataclass
class MailSchema:
    """Discovered shape of the Envelope Index on this machine."""

    tables: dict[str, list[str]] = field(default_factory=dict)

    def has_table(self, name: str) -> bool:
        return name in self.tables

    def columns(self, table: str) -> list[str]:
        return self.tables.get(table, [])

    def has_column(self, table: str, column: str) -> bool:
        return column in self.tables.get(table, [])

    def first_column(self, table: str, candidates: list[str]) -> str | None:
        """Return the first of ``candidates`` that exists in ``table``."""
        cols = self.tables.get(table, [])
        for c in candidates:
            if c in cols:
                return c
        return None


def load_schema(conn: sqlite3.Connection) -> MailSchema:
    schema = MailSchema()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    for row in rows:
        table = row["name"]
        if table.startswith("sqlite_"):
            continue
        cols = [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        schema.tables[table] = cols
    return schema

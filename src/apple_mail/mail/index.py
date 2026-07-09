"""Adaptive SQL over the Envelope Index.

Apple Mail's schema drifts between macOS releases, so this module resolves the
real column names at runtime (via :class:`MailSchema`) and builds queries from
whatever is present. Missing columns degrade to ``NULL`` rather than crashing.
Verified against macOS 26 / Mail V10; ``apple-mail mail schema`` dumps what was found.

All output here is *lightweight* (the tiered-output rule): subject, a cheap
preview when the store has one, dates, mailbox, flags, and the full participant
set (from/to/cc/bcc). Full bodies are never read here - that is the job of
``mail read`` via the .emlx drill-down.

Notes on the V10 shape this was validated against:
- ``messages.subject`` is a FK into ``subjects``; ``messages.subject_prefix``
  holds the "Re: "/"Fw: " part and must be prepended.
- ``messages.summary`` is a FK into ``summaries`` (sparse; ~2% of messages).
- the RFC Message-ID header lives in ``message_global_data.message_id_header``
  (``messages.message_id`` is an internal 64-bit hash, not the header).
- attachments are listed in the ``attachments`` table, not a count column.
- ``recipients.type``: 0 = to, 1 = cc, 2 = bcc (only 0/1 seen in practice).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import unquote

from ..errors import AppleMailError
from ..timeparse import store_to_local_iso
from .store import MailSchema

_CF_EPOCH = 978307200

# Apple Mail recipients.type convention (verify via `apple-mail mail schema`).
_RECIPIENT_TYPES = {0: "to", 1: "cc", 2: "bcc"}


def _mailbox_name(url: str | None) -> str | None:
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return unquote(tail) or None


class MailIndex:
    def __init__(self, conn: sqlite3.Connection, schema: MailSchema, version_dir: Path):
        self.conn = conn
        self.schema = schema
        self.version_dir = version_dir
        if not schema.has_table("messages"):
            raise AppleMailError(
                "MAIL_STORE_ERROR",
                "Envelope Index has no 'messages' table; schema is unexpected.",
                details={"tables": list(schema.tables)},
            )
        self._resolve_columns()
        self._cf_sub = self._detect_cf_offset()

    # -- schema resolution ----------------------------------------------------

    def _resolve_columns(self) -> None:
        fc = self.schema.first_column
        hc = self.schema.has_column
        ht = self.schema.has_table

        self.c_subject = fc("messages", ["subject"])
        self.c_subject_prefix = fc("messages", ["subject_prefix"])
        self.c_sender = fc("messages", ["sender"])
        self.c_drecv = fc("messages", ["date_received", "date_last_viewed", "date_created"])
        self.c_dsent = fc("messages", ["date_sent"])
        self.c_mailbox = fc("messages", ["mailbox"])
        self.c_conv = fc("messages", ["conversation_id", "conversation"])
        self.c_read = fc("messages", ["read"])
        self.c_flagged = fc("messages", ["flagged"])
        self.c_flags = fc("messages", ["flags"])
        self.c_size = fc("messages", ["size"])
        self.c_deleted = fc("messages", ["deleted"])
        self.c_msgid_num = fc("messages", ["message_id"])

        self.has_subjects = ht("subjects") and hc("subjects", "subject")
        self.has_addresses = ht("addresses")
        self.has_mailboxes = ht("mailboxes")
        self.has_recipients = ht("recipients")
        self.has_attachments = ht("attachments") and hc("attachments", "message")

        # preview/snippet: a literal text column (older macOS), else a FK into
        # the summaries table (V10+). Both are handled.
        self.c_snippet_text = fc("messages", ["snippet", "preview"])
        self.summary_fk = (
            "summary"
            if hc("messages", "summary") and ht("summaries") and hc("summaries", "summary")
            else None
        )

        # real RFC Message-ID header (V10+).
        self.has_mgd_header = (
            ht("message_global_data")
            and hc("message_global_data", "message_id_header")
            and hc("message_global_data", "message_id")
            and hc("messages", "message_id")
        )

        if self.has_recipients:
            self.r_message = fc("recipients", ["message"])
            self.r_type = fc("recipients", ["type"])
            self.r_address = fc("recipients", ["address"])
            self.r_position = fc("recipients", ["position"])

    def _detect_cf_offset(self) -> int:
        """Return seconds to subtract from a Unix threshold to match stored dates."""
        if not self.c_drecv:
            return 0
        try:
            row = self.conn.execute(
                f"SELECT MAX({self.c_drecv}) AS m FROM messages"
            ).fetchone()
        except sqlite3.Error:
            return 0
        top = row["m"] if row else None
        if top is not None and 0 < float(top) < 1_000_000_000:
            return _CF_EPOCH
        return 0

    # -- query building -------------------------------------------------------

    def _subject_expr(self) -> str:
        base = (
            "s.subject"
            if self.has_subjects
            else (f"m.{self.c_subject}" if self.c_subject else "NULL")
        )
        if self.c_subject_prefix:
            return f"(COALESCE(m.{self.c_subject_prefix},'') || COALESCE({base},'')) AS subject"
        return f"{base} AS subject"

    def _snippet_expr(self) -> str:
        if self.c_snippet_text:
            return f"m.{self.c_snippet_text} AS snippet"
        if self.summary_fk:
            return "sm.summary AS snippet"
        return "NULL AS snippet"

    def _select_clause(self) -> str:
        def m(col, alias):
            return f"m.{col} AS {alias}" if col else f"NULL AS {alias}"

        parts = [
            "m.ROWID AS id",
            "gd.message_id_header AS message_id" if self.has_mgd_header else "NULL AS message_id",
            self._subject_expr(),
            m(self.c_drecv, "date_received"),
            m(self.c_dsent, "date_sent"),
            m(self.c_conv, "conversation_id"),
            m(self.c_read, "is_read"),
            m(self.c_flagged, "is_flagged"),
            m(self.c_flags, "flags"),
            m(self.c_size, "size"),
            self._snippet_expr(),
            m(self.c_mailbox, "mailbox_id"),
            "mb.url AS mailbox_url" if self.has_mailboxes else "NULL AS mailbox_url",
            "sa.address AS from_address" if self.has_addresses else "NULL AS from_address",
            "sa.comment AS from_name" if self.has_addresses else "NULL AS from_name",
        ]
        return ", ".join(parts)

    def _from_clause(self) -> str:
        joins = ["FROM messages m"]
        if self.has_subjects and self.c_subject:
            joins.append(f"LEFT JOIN subjects s ON s.ROWID = m.{self.c_subject}")
        if self.has_addresses and self.c_sender:
            joins.append(f"LEFT JOIN addresses sa ON sa.ROWID = m.{self.c_sender}")
        if self.has_mailboxes and self.c_mailbox:
            joins.append(f"LEFT JOIN mailboxes mb ON mb.ROWID = m.{self.c_mailbox}")
        if self.summary_fk:
            joins.append(f"LEFT JOIN summaries sm ON sm.ROWID = m.{self.summary_fk}")
        if self.has_mgd_header:
            joins.append("LEFT JOIN message_global_data gd ON gd.message_id = m.message_id")
        return " ".join(joins)

    def _order_clause(self) -> str:
        if self.c_drecv:
            return f"ORDER BY m.{self.c_drecv} DESC"
        return "ORDER BY m.ROWID DESC"

    def _build_where(
        self,
        *,
        mailbox=None,
        after=None,
        before=None,
        unread=False,
        query=None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []

        # Never surface expunge-pending (deleted) messages in list/search.
        if self.c_deleted:
            clauses.append(f"m.{self.c_deleted} = 0")

        if mailbox is not None and self.c_mailbox:
            ids = self._resolve_mailbox_ids(mailbox)
            if not ids:
                raise AppleMailError("NOT_FOUND", f"no mailbox matching {mailbox!r}")
            placeholders = ",".join("?" for _ in ids)
            clauses.append(f"m.{self.c_mailbox} IN ({placeholders})")
            params.extend(ids)

        if after is not None and self.c_drecv:
            clauses.append(f"m.{self.c_drecv} >= ?")
            params.append(after - self._cf_sub)
        if before is not None and self.c_drecv:
            clauses.append(f"m.{self.c_drecv} <= ?")
            params.append(before - self._cf_sub)

        if unread and self.c_read:
            clauses.append(f"m.{self.c_read} = 0")

        if query:
            like = f"%{query}%"
            ors: list[str] = []
            if self.has_subjects:
                ors.append("s.subject LIKE ?")
                params.append(like)
            elif self.c_subject:
                ors.append(f"m.{self.c_subject} LIKE ?")
                params.append(like)
            if self.has_addresses:
                ors.append("sa.address LIKE ?")
                params.append(like)
                ors.append("sa.comment LIKE ?")
                params.append(like)
            if self.has_recipients and self.has_addresses:
                ors.append(
                    f"m.ROWID IN (SELECT r.{self.r_message} FROM recipients r "
                    f"JOIN addresses a ON a.ROWID = r.{self.r_address} "
                    "WHERE a.address LIKE ? OR a.comment LIKE ?)"
                )
                params.extend([like, like])
            if self.c_snippet_text:
                ors.append(f"m.{self.c_snippet_text} LIKE ?")
                params.append(like)
            elif self.summary_fk:
                ors.append("sm.summary LIKE ?")
                params.append(like)
            if ors:
                clauses.append("(" + " OR ".join(ors) + ")")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # -- public API -----------------------------------------------------------

    def list_messages(
        self,
        *,
        mailbox=None,
        after=None,
        before=None,
        unread=False,
        query=None,
        limit=50,
        offset=0,
    ) -> tuple[list[dict], bool]:
        where, params = self._build_where(
            mailbox=mailbox, after=after, before=before, unread=unread, query=query
        )
        sql = (
            f"SELECT {self._select_clause()} {self._from_clause()} "
            f"{where} {self._order_clause()} LIMIT ? OFFSET ?"
        )
        rows = self.conn.execute(sql, [*params, limit + 1, offset]).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [self._row_to_item(r) for r in rows]
        self._attach_recipients(items)
        self._attach_attachments(items)
        return items, has_more

    def get_meta(self, rowid: int) -> dict | None:
        sql = f"SELECT {self._select_clause()} {self._from_clause()} WHERE m.ROWID = ? LIMIT 1"
        row = self.conn.execute(sql, [rowid]).fetchone()
        if row is None:
            return None
        item = self._row_to_item(row)
        self._attach_recipients([item])
        self._attach_attachments([item])
        return item

    def mailboxes(self) -> list[dict]:
        if not self.has_mailboxes:
            return []
        cols = self.schema.columns("mailboxes")
        total = "total_count" if "total_count" in cols else None
        unread = "unread_count" if "unread_count" in cols else None
        url = "url" if "url" in cols else None
        select = ["ROWID AS id"]
        select.append(f"{url} AS url" if url else "NULL AS url")
        select.append(f"{total} AS total" if total else "NULL AS total")
        select.append(f"{unread} AS unread" if unread else "NULL AS unread")
        sql = f"SELECT {', '.join(select)} FROM mailboxes ORDER BY url"
        out = []
        for r in self.conn.execute(sql).fetchall():
            out.append(
                {
                    "id": r["id"],
                    "name": _mailbox_name(r["url"]),
                    "url": r["url"],
                    "total": r["total"],
                    "unread": r["unread"],
                }
            )
        return out

    def attachments_for(self, rowid: int) -> list[dict]:
        """Authoritative attachment list from the attachments table (name + id).

        This catches inline parts (e.g. meeting .ics) that the MIME walker skips.
        """
        if not self.has_attachments:
            return []
        cols = self.schema.columns("attachments")
        name = "name" if "name" in cols else None
        aid = "attachment_id" if "attachment_id" in cols else None
        select = ["ROWID AS rid"]
        select.append(f"{name} AS name" if name else "NULL AS name")
        select.append(f"{aid} AS attachment_id" if aid else "NULL AS attachment_id")
        rows = self.conn.execute(
            f"SELECT {', '.join(select)} FROM attachments WHERE message = ? ORDER BY ROWID",
            [rowid],
        ).fetchall()
        return [{"attachmentId": r["attachment_id"], "name": r["name"]} for r in rows]

    # -- helpers --------------------------------------------------------------

    def _resolve_mailbox_ids(self, mailbox) -> list[int]:
        if isinstance(mailbox, int) or (isinstance(mailbox, str) and mailbox.isdigit()):
            return [int(mailbox)]
        if not self.has_mailboxes:
            return []
        like = f"%{mailbox}%"
        rows = self.conn.execute(
            "SELECT ROWID AS id FROM mailboxes WHERE url LIKE ?", [like]
        ).fetchall()
        return [r["id"] for r in rows]

    def _row_to_item(self, r: sqlite3.Row) -> dict:
        recv_iso = store_to_local_iso(r["date_received"])
        sent_iso = store_to_local_iso(r["date_sent"])
        from_obj = None
        if r["from_address"] or r["from_name"]:
            from_obj = {"name": r["from_name"] or None, "address": r["from_address"] or None}
        return {
            "id": r["id"],
            "messageId": r["message_id"],
            "conversationId": r["conversation_id"],
            "subject": r["subject"],
            "preview": r["snippet"],
            "date": recv_iso,
            "dateReceived": recv_iso,
            "dateSent": sent_iso,
            "mailbox": {"id": r["mailbox_id"], "name": _mailbox_name(r["mailbox_url"])},
            "from": from_obj,
            "to": [],
            "cc": [],
            "bcc": [],
            "isUnread": (r["is_read"] == 0) if r["is_read"] is not None else None,
            "isFlagged": bool(r["is_flagged"]) if r["is_flagged"] is not None else None,
            "hasAttachments": None,
            "size": r["size"],
        }

    def _attach_recipients(self, items: list[dict]) -> None:
        if not items or not self.has_recipients or not self.has_addresses:
            return
        by_id = {it["id"]: it for it in items}
        placeholders = ",".join("?" for _ in by_id)
        order = f"ORDER BY r.{self.r_message}"
        if self.r_position:
            order += f", r.{self.r_position}"
        sql = (
            f"SELECT r.{self.r_message} AS mid, r.{self.r_type} AS rtype, "
            "a.address AS address, a.comment AS name "
            f"FROM recipients r LEFT JOIN addresses a ON a.ROWID = r.{self.r_address} "
            f"WHERE r.{self.r_message} IN ({placeholders}) {order}"
        )
        seen: dict[tuple, set] = {}
        for row in self.conn.execute(sql, list(by_id)).fetchall():
            item = by_id.get(row["mid"])
            if item is None:
                continue
            bucket = _RECIPIENT_TYPES.get(row["rtype"], "to")
            if bucket not in ("to", "cc", "bcc"):
                bucket = "to"
            addr = (row["address"] or "").strip().lower()
            key = (row["mid"], bucket)
            dedup = seen.setdefault(key, set())
            if addr and addr in dedup:
                continue
            if addr:
                dedup.add(addr)
            item[bucket].append({"name": row["name"] or None, "address": row["address"] or None})

    def _attach_attachments(self, items: list[dict]) -> None:
        if not items or not self.has_attachments:
            return
        by_id = {it["id"]: it for it in items}
        for it in items:
            it["hasAttachments"] = False
        placeholders = ",".join("?" for _ in by_id)
        rows = self.conn.execute(
            f"SELECT DISTINCT message AS mid FROM attachments WHERE message IN ({placeholders})",
            list(by_id),
        ).fetchall()
        for row in rows:
            it = by_id.get(row["mid"])
            if it is not None:
                it["hasAttachments"] = True

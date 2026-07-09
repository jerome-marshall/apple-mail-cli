"""``apple-mail mail`` command handlers.

Each handler returns the ``data`` payload (a dict, or a list-payload built with
``envelope.list_payload``); the CLI wraps it in the ``{"ok": true, ...}`` envelope
and formats it. Everything here is read-only.
"""

from __future__ import annotations

import sys

from ..envelope import list_payload
from ..errors import AppleMailError, not_found, validation
from ..timeparse import to_epoch
from .emlx import find_emlx, parse_emlx
from .index import MailIndex
from .store import is_macos, load_schema, open_index


def _open() -> tuple[MailIndex, object]:
    conn, version_dir = open_index()
    schema = load_schema(conn)
    return MailIndex(conn, schema, version_dir), conn


def _coerce_id(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise validation(f"message id must be an integer, got {value!r}") from None


def cmd_list(args) -> dict:
    idx, conn = _open()
    try:
        after = to_epoch(args.after) if args.after else None
        before = to_epoch(args.before) if args.before else None
        items, has_more = idx.list_messages(
            mailbox=args.mailbox,
            after=after,
            before=before,
            unread=args.unread,
            limit=args.limit,
            offset=args.offset,
        )
        return list_payload(items, has_more=has_more)
    finally:
        conn.close()


def cmd_search(args) -> dict:
    query = (args.query or "").strip()
    if not query:
        raise validation("search needs a non-empty query string")
    idx, conn = _open()
    try:
        after = to_epoch(args.after) if args.after else None
        before = to_epoch(args.before) if args.before else None
        items, has_more = idx.list_messages(
            mailbox=args.mailbox,
            after=after,
            before=before,
            unread=args.unread,
            query=query,
            limit=args.limit,
            offset=args.offset,
        )
        return list_payload(items, has_more=has_more)
    finally:
        conn.close()


def cmd_mailboxes(args) -> dict:
    idx, conn = _open()
    try:
        return list_payload(idx.mailboxes())
    finally:
        conn.close()


def _merge_attachments(emlx_atts: list[dict], index_atts: list[dict]) -> list[dict]:
    """Use the index attachments table as the authoritative spine, enriching each
    with contentType/size from the parsed .emlx where the filename matches."""
    if not index_atts:
        return emlx_atts
    by_name = {(a.get("filename") or "").strip().lower(): a for a in emlx_atts}
    merged: list[dict] = []
    for ia in index_atts:
        nm = ia.get("name") or ""
        e = by_name.get(nm.strip().lower())
        merged.append(
            {
                "filename": nm or (e.get("filename") if e else None),
                "contentType": e.get("contentType") if e else None,
                "size": e.get("size") if e else None,
                "attachmentId": ia.get("attachmentId"),
            }
        )
    index_names = {(ia.get("name") or "").strip().lower() for ia in index_atts}
    for e in emlx_atts:
        if (e.get("filename") or "").strip().lower() not in index_names:
            merged.append({**e, "attachmentId": None})
    return merged


def cmd_read(args) -> dict:
    rowid = _coerce_id(args.id)
    idx, conn = _open()
    try:
        meta = idx.get_meta(rowid)
        version_dir = idx.version_dir
        index_atts = idx.attachments_for(rowid) if meta is not None else []
    finally:
        conn.close()
    if meta is None:
        raise not_found(f"no message with id {rowid}")

    result = dict(meta)
    path = find_emlx(version_dir, rowid)
    if path is None:
        result.update(
            {
                "body": None,
                "attachments": [
                    {"filename": a.get("name"), "contentType": None, "size": None,
                     "attachmentId": a.get("attachmentId")}
                    for a in index_atts
                ],
                "bodyAvailable": False,
                "source": "index",
                "warning": (
                    "Body is not stored locally for this message. In Apple Mail, set the "
                    "account to download full messages (not 'recent only') and let it sync."
                ),
            }
        )
        return result

    parsed = parse_emlx(path)
    result.update(
        {
            "subject": parsed["subject"] or meta.get("subject"),
            "messageId": parsed["messageId"] or meta.get("messageId"),
            "inReplyTo": parsed["inReplyTo"],
            "from": parsed["from"] or meta.get("from"),
            "to": parsed["to"] or meta.get("to"),
            "cc": parsed["cc"] or meta.get("cc"),
            "bcc": parsed["bcc"] or meta.get("bcc"),
            "replyTo": parsed["replyTo"],
            "headers": parsed["headers"],
            "body": parsed["body"],
            "attachments": _merge_attachments(parsed["attachments"], index_atts),
            "bodyAvailable": True,
            "emlxPath": str(path),
            "source": "emlx",
        }
    )
    return result


def cmd_schema(args) -> dict:
    """Dump the discovered Envelope Index schema. Run this first on a new machine
    to confirm column names and the recipients.type mapping."""
    idx, conn = _open()
    try:
        rec_types = []
        if idx.has_recipients and idx.r_type:
            rows = conn.execute(
                f"SELECT r.{idx.r_type} AS t, COUNT(*) AS c FROM recipients r "
                f"GROUP BY r.{idx.r_type} ORDER BY c DESC"
            ).fetchall()
            rec_types = [{"type": r["t"], "count": r["c"]} for r in rows]
        msg_count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {
            "versionDir": str(idx.version_dir),
            "messageCount": msg_count,
            "cfTimeOffsetApplied": idx._cf_sub != 0,
            "resolvedColumns": {
                "subject": idx.c_subject,
                "subject_prefix": idx.c_subject_prefix,
                "sender": idx.c_sender,
                "date_received": idx.c_drecv,
                "date_sent": idx.c_dsent,
                "mailbox": idx.c_mailbox,
                "conversation": idx.c_conv,
                "read": idx.c_read,
                "flagged": idx.c_flagged,
                "flags": idx.c_flags,
                "size": idx.c_size,
                "message_id_numeric": idx.c_msgid_num,
                "message_id_header": (
                    "message_global_data.message_id_header" if idx.has_mgd_header else None
                ),
                "snippet_text": idx.c_snippet_text,
                "summary_fk": idx.summary_fk,
                "attachments_table": idx.has_attachments,
            },
            "recipientTypeDistribution": rec_types,
            "tables": idx.schema.tables,
        }
    finally:
        conn.close()


def health() -> dict:
    """Mail-side health for ``apple-mail doctor``."""
    info: dict = {"ok": False}
    try:
        conn, version_dir = open_index()
        schema = load_schema(conn)
        MailIndex(conn, schema, version_dir)
        count = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        conn.close()
        info = {"ok": True, "versionDir": str(version_dir), "messageCount": count}
    except AppleMailError as exc:
        info = {"ok": False, "code": exc.code, "message": exc.message}
    return info

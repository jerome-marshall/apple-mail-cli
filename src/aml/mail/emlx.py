"""Parse Apple Mail ``.emlx`` files and locate them on disk.

An ``.emlx`` file is:

    <ascii-decimal-byte-count>\\n
    <that many bytes of raw RFC822 message>
    <an Apple XML property-list trailer with local metadata>

``.partial.emlx`` has the same framing; only large attachments are stored out of
line, but the headers and text body are still present, which is all we need.

Locating the file: the filename is ``<ROWID>.emlx`` (or ``<ROWID>.partial.emlx``)
where ROWID is the Envelope Index ``messages`` row id. The sharded directory
layout differs across macOS versions, so rather than guess it we walk the version
directory once and stop at the first match. This only happens on an explicit
``mail read``/``get`` drill-down, never during list/search.
"""

from __future__ import annotations

import os
import plistlib
from email import message_from_bytes
from email.policy import default as default_policy
from email.utils import getaddresses
from pathlib import Path

from ..errors import full_disk_access


def find_emlx(version_dir: Path, rowid: int) -> Path | None:
    full = f"{rowid}.emlx"
    partial = f"{rowid}.partial.emlx"
    try:
        for dirpath, _dirnames, filenames in os.walk(version_dir):
            names = set(filenames)
            if full in names:
                return Path(dirpath) / full
            if partial in names:
                return Path(dirpath) / partial
    except PermissionError:
        raise full_disk_access() from None
    return None


def _addr_list(header_values) -> list[dict]:
    strings = [str(v) for v in (header_values or [])]
    out: list[dict] = []
    for name, addr in getaddresses(strings):
        if name or addr:
            out.append({"name": name or None, "address": (addr or None)})
    return out


def _first_addr(header_values) -> dict | None:
    lst = _addr_list(header_values)
    return lst[0] if lst else None


def _decode_part(part) -> str | None:
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True)
        if payload is None:
            return None
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return content


def parse_emlx(path: Path) -> dict:
    """Parse an .emlx file into a structured dict (headers, bodies, attachments)."""
    try:
        raw = path.read_bytes()
    except PermissionError:
        raise full_disk_access() from None

    newline = raw.find(b"\n")
    if newline == -1:
        raise ValueError(f"{path.name}: not a valid .emlx file (no length prefix)")
    try:
        length = int(raw[:newline].strip())
    except ValueError:
        raise ValueError(f"{path.name}: invalid .emlx length prefix") from None

    body_start = newline + 1
    msg_bytes = raw[body_start : body_start + length]
    trailer = raw[body_start + length :]

    msg = message_from_bytes(msg_bytes, policy=default_policy)

    text_part = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    text = _decode_part(text_part) if text_part is not None else None
    html = _decode_part(html_part) if html_part is not None else None

    attachments: list[dict] = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        attachments.append(
            {
                "filename": part.get_filename(),
                "contentType": part.get_content_type(),
                "size": len(payload) if payload is not None else None,
            }
        )

    apple_flags = None
    if trailer.strip():
        try:
            meta = plistlib.loads(trailer)
            if isinstance(meta, dict):
                apple_flags = meta.get("flags")
        except Exception:  # noqa: BLE001 - trailer is best-effort metadata
            apple_flags = None

    return {
        "subject": msg["subject"],
        "from": _first_addr(msg.get_all("from")),
        "to": _addr_list(msg.get_all("to")),
        "cc": _addr_list(msg.get_all("cc")),
        "bcc": _addr_list(msg.get_all("bcc")),
        "replyTo": _addr_list(msg.get_all("reply-to")),
        "date": msg["date"],
        "messageId": msg["message-id"],
        "inReplyTo": msg["in-reply-to"],
        "headers": {
            "from": msg["from"],
            "to": msg["to"],
            "cc": msg["cc"],
            "subject": msg["subject"],
            "date": msg["date"],
        },
        "body": {"text": text, "html": html},
        "attachments": attachments,
        "appleFlags": apple_flags,
    }

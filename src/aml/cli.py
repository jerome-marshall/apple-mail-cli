"""``aml`` command-line entrypoint.

Read-only by construction: there are no send/move/delete/flag/create commands.
Global output flags (``--json`` default, ``--ndjson``, ``--toon``) live on each
leaf command. Run ``aml doctor`` once at the start of a session.
"""

from __future__ import annotations

import argparse
import platform
import sys

from . import __version__
from .cal import commands as cal_cmds
from .cal import store as cal_store
from .envelope import JSON, NDJSON, TOON, emit_error, emit_success
from .errors import AmlError
from .mail import commands as mail_cmds


def _output_parent() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(add_help=False)
    group = out.add_mutually_exclusive_group()
    group.add_argument("--json", dest="fmt", action="store_const", const=JSON,
                       help="JSON envelope (default; pretty on a TTY)")
    group.add_argument("--ndjson", dest="fmt", action="store_const", const=NDJSON,
                       help="newline-delimited JSON (one item per line for lists)")
    group.add_argument("--toon", dest="fmt", action="store_const", const=TOON,
                       help="TOON encoding (token-efficient, for LLM prompts)")
    out.set_defaults(fmt=JSON)
    return out


def _page_parent() -> argparse.ArgumentParser:
    page = argparse.ArgumentParser(add_help=False)
    page.add_argument("--limit", type=int, default=50, help="max results (default 50)")
    page.add_argument("--offset", type=int, default=0, help="results to skip")
    return page


def _cmd_version(args) -> dict:
    return {"name": "aml", "version": __version__}


def _cmd_doctor(args) -> dict:
    return {
        "amlVersion": __version__,
        "python": platform.python_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "ok": sys.platform == "darwin",
        },
        "mail": mail_cmds.health(),
        "calendar": cal_store.health(),
    }


def build_parser() -> argparse.ArgumentParser:
    out = _output_parent()
    page = _page_parent()

    parser = argparse.ArgumentParser(
        prog="aml",
        description="Read-only, JSON-first CLI over local Apple Mail + Calendar.",
    )
    parser.add_argument("--version", action="version", version=f"aml {__version__}")
    parser.set_defaults(func=None)
    top = parser.add_subparsers(dest="group")

    # version / doctor
    pv = top.add_parser("version", parents=[out], help="print version")
    pv.set_defaults(func=_cmd_version)
    pd = top.add_parser("doctor", parents=[out], help="health check (run once per session)")
    pd.set_defaults(func=_cmd_doctor)

    # ---- mail ----
    mail = top.add_parser("mail", help="read mail")
    mail.set_defaults(func=None)
    msub = mail.add_subparsers(dest="cmd")

    m_list = msub.add_parser("list", parents=[out, page], help="list messages (lightweight)")
    m_list.add_argument("--mailbox", help="mailbox id or name substring")
    m_list.add_argument("--after", help="ISO datetime (naked=local, Z=UTC)")
    m_list.add_argument("--before", help="ISO datetime (naked=local, Z=UTC)")
    m_list.add_argument("--unread", action="store_true", help="only unread")
    m_list.set_defaults(func=mail_cmds.cmd_list)

    m_search = msub.add_parser("search", parents=[out, page],
                               help="search subject/sender/participants (lightweight)")
    m_search.add_argument("query", help="text to match")
    m_search.add_argument("--mailbox", help="mailbox id or name substring")
    m_search.add_argument("--after", help="ISO datetime (naked=local, Z=UTC)")
    m_search.add_argument("--before", help="ISO datetime (naked=local, Z=UTC)")
    m_search.add_argument("--unread", action="store_true", help="only unread")
    m_search.set_defaults(func=mail_cmds.cmd_search)

    m_read = msub.add_parser("read", parents=[out], help="full message drill-down (.emlx)")
    m_read.add_argument("id", help="message id from list/search")
    m_read.set_defaults(func=mail_cmds.cmd_read)

    m_get = msub.add_parser("get", parents=[out], help="alias of read")
    m_get.add_argument("id", help="message id from list/search")
    m_get.set_defaults(func=mail_cmds.cmd_read)

    m_mb = msub.add_parser("mailboxes", parents=[out], help="list mailboxes/folders")
    m_mb.set_defaults(func=mail_cmds.cmd_mailboxes)

    m_sc = msub.add_parser("schema", parents=[out],
                           help="dump discovered Envelope Index schema (diagnostics)")
    m_sc.set_defaults(func=mail_cmds.cmd_schema)

    # ---- cal ----
    cal = top.add_parser("cal", help="read calendar")
    cal.set_defaults(func=None)
    csub = cal.add_subparsers(dest="cmd")

    c_cals = csub.add_parser("calendars", parents=[out], help="list calendars")
    c_cals.set_defaults(func=cal_cmds.cmd_calendars)

    c_list = csub.add_parser("list", parents=[out], help="list events in a window (lightweight)")
    c_list.add_argument("--start", help="ISO datetime (naked=local)")
    c_list.add_argument("--end", help="ISO datetime (naked=local)")
    c_list.add_argument("--days", type=int, help="from now through N days ahead (default 7)")
    c_list.add_argument("--calendar", action="append",
                        help="restrict to calendar title/id (repeatable)")
    c_list.add_argument("--limit", type=int, default=None, help="max events")
    c_list.set_defaults(func=cal_cmds.cmd_list)

    c_get = csub.add_parser("get", parents=[out], help="full event drill-down")
    c_get.add_argument("id", help="event identifier from cal list")
    c_get.set_defaults(func=cal_cmds.cmd_get)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fmt = getattr(args, "fmt", JSON)
    func = getattr(args, "func", None)

    if func is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        data = func(args)
    except AmlError as exc:
        emit_error(exc, fmt)
        return exc.exit_code
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - last-resort envelope
        emit_error(AmlError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"), fmt)
        return 1

    emit_success(data, fmt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

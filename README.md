# `aml` — read-only Apple Mail + Calendar CLI

`aml` ("Apple Mail/calendar") is a JSON-first, one-shot command-line tool that
reads the **local** Apple Mail and Calendar stores. It exists to give AI agents
**reliable, read-only** access to an Outlook **work** mailbox + calendar that has
been added to macOS via **Internet Accounts** (the OS does the OAuth — no Azure
app registration, no IT/admin, no Graph/EWS/IMAP).

It replaces the flaky AppleScript-driven `olk` CLI. Because `aml` reads static
files (the Mail SQLite "Envelope Index" + `.emlx` message files) and queries
EventKit directly, there is no per-call IPC tax and no app to keep running.

**It is read-only by construction.** There is no code path that sends, replies,
moves, deletes, flags, or otherwise mutates anything.

## How it works

| Data        | Source                                                                 |
|-------------|------------------------------------------------------------------------|
| Mail metadata / search / threading | `~/Library/Mail/V<N>/MailData/Envelope Index` (SQLite), queried once per command |
| Full mail bodies (drill-down)      | `.emlx` files under `~/Library/Mail/V<N>/...` (RFC822)            |
| Calendar                           | `~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb` (SQLite). Recurring events are read from the `OccurrenceCache` macOS maintains, so instances expand correctly without RRULE math. |

The Envelope Index schema changes between macOS releases, so `aml` **introspects
the schema at runtime** (table/column names, the `recipients.type` mapping, and
the date encoding) rather than hardcoding it. Run `aml mail schema` to see what
it discovered on this machine.

## Prerequisites (one-time, no IT needed)

1. **The work account is added to Apple Mail.** System Settings → Internet
   Accounts → add the Exchange/work account, enable **Mail** and **Calendars**,
   and let it sync. In Mail → Settings → Accounts → Account Information, set it to
   **download full messages** (not "recent only") so bodies are local.
2. **Full Disk Access** for the app that runs `aml` (Cursor / iTerm / Terminal):
   System Settings → Privacy & Security → **Full Disk Access** → toggle the app
   on, then **fully quit and reopen** it. This is what lets `aml` read both
   `~/Library/Mail` **and** the Calendar store. Direct file reads need *only* FDA —
   no Automation grant and **no separate Calendar permission**, since we read the
   SQLite store rather than scripting the apps or using EventKit.

Check everything at once:

```bash
aml doctor
```

## Install

```bash
pipx install ~/Workspace/aml      # or: pipx install -e ~/Workspace/aml for dev
```

No third-party dependencies — both mail and calendar read local SQLite stores
using only the Python standard library.

## Output contract

Every command prints one envelope:

```jsonc
{ "ok": true, "data": <object> }                                  // scalar
{ "ok": true, "data": { "items": [...], "count": N, "hasMore": b } } // list
{ "ok": false, "error": { "code": "...", "message": "..." } }     // error (stderr, exit != 0)
```

Output modes (pick one per call):

- `--json` (default) — pretty on a TTY, compact when piped.
- `--ndjson` — one object per line; for lists, one **item** per line.
- `--toon` — token-efficient [TOON](https://toonformat.dev) for LLM prompts.

## Tiered output (important)

`mail list` and `mail search` are **lightweight by default**: subject (with the
`Re:`/`Fw:` prefix), a short preview when the store has one (sparse — Apple only
keeps previews for some messages), dates, mailbox, flags, the real RFC
`messageId`, `conversationId`, and **all participants** (from/to/cc/bcc). They do
**not** read message bodies. Fetch the full body only with an explicit drill-down:

```bash
aml mail read <id>     # parses the .emlx — headers + full body + attachments
```

`list`/`search` span **all mailboxes** by date and exclude expunge-pending
deleted messages. Scope to one folder with `--mailbox Inbox` (name substring or
id from `aml mail mailboxes`); an unscoped list interleaves Sent and other folders.

## Commands

```text
aml doctor                       # health: platform, FDA, mail store, calendar auth
aml version

aml mail list   [--mailbox X] [--after ISO] [--before ISO] [--unread] [--limit N] [--offset N]
aml mail search "<query>" [--mailbox X] [--after ISO] [--before ISO] [--unread] [--limit N]
aml mail read   <id>             # full drill-down (alias: aml mail get <id>)
aml mail mailboxes               # list mailboxes/folders
aml mail schema                  # diagnostics: discovered tables/columns + recipient types

aml cal calendars
aml cal list   [--start ISO --end ISO | --days N] [--calendar NAME ...] [--limit N]
aml cal get    <id>              # full event: all attendees + notes
```

## Date/time semantics (same as `olk`)

- **Naked ISO** (`2026-06-24T00:00:00`) → **local** time. Use this for today/tomorrow.
- **Trailing `Z`** → UTC. **Explicit offset** (`+05:30`) → that offset.
- Bare `YYYY-MM-DD` → local midnight.

```bash
TODAY=$(date +%Y-%m-%d)
aml mail list --after "${TODAY}T00:00:00" --before "${TODAY}T23:59:59" --limit 200
aml cal list --days 7
```

## Error codes

| Code                        | Meaning / fix                                                  |
|-----------------------------|----------------------------------------------------------------|
| `FULL_DISK_ACCESS_REQUIRED` | Grant Full Disk Access, then reopen the app.                    |
| `MAIL_STORE_NOT_FOUND`      | No `~/Library/Mail/V<N>`; add the account to Apple Mail.        |
| `CALENDAR_STORE_NOT_FOUND`  | No Calendar store; add the account's Calendar in Internet Accounts. |
| `NOT_FOUND`                 | Bad/stale id; re-list.                                          |
| `VALIDATION_ERROR`          | Bad arguments; check `--help`.                                  |
| `PLATFORM_UNSUPPORTED`      | macOS only.                                                     |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -U pytest
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m aml doctor
```

## Scope

- **In:** all mail (lightweight list/search + full-body drill-down) + calendar
  events and content.
- **Out:** categories, tasks, reminders, notes, contacts. And anything that
  writes — by design.

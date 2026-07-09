---
name: apple-mail-cli
description: >
  Read the user's local Apple Mail + Calendar on macOS with the `apple-mail` CLI:
  summarize, search, or read work email; check the calendar; find a thread, an
  attachment list, or who's on a meeting. `apple-mail` reads the Outlook work account
  that macOS synced into Apple Mail/Calendar (Internet Accounts) — so it needs no
  Graph token, no IMAP, and no admin consent. Trigger on "summarize my emails
  today", "what's on my calendar tomorrow", "did I get a reply from <person>",
  "find the thread about <topic>", "who's on the 3pm meeting" — even when the user
  doesn't say "apple-mail". READ-ONLY: it cannot send, reply, move, delete, flag, or
  schedule; for any write, use the `outlook-cli` (`olk`) skill instead.
---

# Apple Mail/Calendar CLI (`apple-mail`)

`apple-mail` is a JSON-first, **read-only** CLI over the local Apple Mail store (SQLite
"Envelope Index" + `.emlx` files) and the local Calendar store
(`Calendar.sqlitedb`). Each call is one shot — no daemon, no app to keep running,
no AppleScript. It has no write surface; for any write see
[When NOT to use](#when-not-to-use).

## Before you start

Once per session that touches mail/calendar, run `apple-mail doctor --json` and check
`data.mail.ok` / `data.calendar.ok`. Don't re-run it per command.

```bash
apple-mail doctor --json
```

- `FULL_DISK_ACCESS_REQUIRED` — the app running `apple-mail` (Cursor / iTerm / Terminal)
  needs **Full Disk Access**: System Settings → Privacy & Security → Full Disk
  Access → enable it → **fully quit and reopen the app**. One grant covers both
  mail and calendar (calendar reads `Calendar.sqlitedb` directly — there is no
  separate Calendar permission prompt). Tell the user once, then stop hammering.
- `MAIL_STORE_NOT_FOUND` / `CALENDAR_STORE_NOT_FOUND` — the work account (or its
  Calendar) isn't synced into Apple Mail/Calendar yet. Tell the user to add it via
  System Settings → Internet Accounts and let it sync.

## Output contract

```jsonc
{ "ok": true, "data": <object> }                                    // scalar
{ "ok": true, "data": { "items": [...], "count": N, "hasMore": b } } // list
{ "ok": false, "error": { "code": "...", "message": "..." } }       // error → stderr, exit != 0
```

The `code` field is stable; branch on it. Output modes:

- `--json` (default) — pretty on a TTY, compact when piped. Use for code/`jq`.
- `--ndjson` — one object per line; for lists each **item** is its own line.
- `--toon` — token-efficient TOON of the whole envelope. Use when piping the
  result straight into an LLM/sub-agent prompt.

**Parsing pattern.** Save to a temp file and parse with python/`jq` — subjects and
bodies contain quotes, newlines, and emoji that break naive shell parsing.

```bash
apple-mail mail list --limit 20 --json > /tmp/inbox.json
python3 -c "import json; d=json.load(open('/tmp/inbox.json'))['data']; \
  print(*[f\"{m['id']:>7}  {(m.get('from') or {}).get('address','?'):30}  {(m['subject'] or '')[:60]}\" \
         for m in d['items']], sep='\n')"
```

## Tiered output — decide before you drill down

`mail list` and `mail search` are **lightweight by design**: each item has the
subject, a short `preview` (when the store has one — Apple keeps a preview for
only a minority of messages, so this is often `null`), `date`, `mailbox`, flags
(`isUnread`/`isFlagged`/`hasAttachments`), `messageId` (the real RFC header),
`conversationId`, and **all participants** — `from`, `to`, `cc`, `bcc`. They do
**NOT** include the body.

`list`/`search` span **all mailboxes** by date (Inbox, Sent, Archive, …) and
exclude only expunge-pending deleted messages. For inbox-only questions, scope
with `--mailbox Inbox` (or any name substring / mailbox id from
`apple-mail mail mailboxes`); an unscoped list interleaves Sent and other folders.

Fetch the body only when you actually need it — an explicit **drill-down**:

```bash
apple-mail mail read <id>     # parses the .emlx: headers + body.text/body.html + attachments
```

So answer from the list/search shape when you can (sender, subject, participants,
preview); only `read` a specific id when the user needs the body or attachments.

## Date and time semantics

`--after`, `--before`, `--start`, `--end` accept ISO 8601:

- **Naked ISO** (`2026-06-24T00:00:00`) → **local** time. Right for "today"/"tomorrow".
- **Trailing `Z`** → UTC. **Explicit offset** (`+05:30`) → that offset.
- Bare `YYYY-MM-DD` → local midnight.

For "today"/"this week", use naked ISO with explicit start and end of day. Don't
convert to UTC client-side.

```bash
TODAY=$(date +%Y-%m-%d)
apple-mail mail list --after "${TODAY}T00:00:00" --before "${TODAY}T23:59:59" --limit 200

apple-mail cal list --days 7          # now through 7 days ahead
TOMORROW=$(date -v+1d +%Y-%m-%d)
apple-mail cal list --start "${TOMORROW}T00:00:00" --end "${TOMORROW}T23:59:59"
```

## Command surface

| Group  | Subcommands |
|--------|-------------|
| `mail` | `list`, `search`, `read` (alias `get`), `mailboxes`, `schema` |
| `cal`  | `calendars`, `list`, `get` |
| top    | `doctor`, `version` |

Common flags: `--limit`/`--offset` on list/search; `--mailbox`, `--after`,
`--before`, `--unread` on mail list/search; `--start`/`--end`/`--days`/`--calendar`
on cal list. Run `apple-mail <group> <cmd> --help` for the canonical flags. `apple-mail mail
schema` dumps the discovered SQLite schema — useful if results look wrong after a
macOS update. `cal get <id>` on a recurring event returns the series master;
per-occurrence times come from `cal list`.

## Recipes

Copy-paste recipes for the common tasks — today's mail summary, find mail from a
person about a topic, tomorrow's calendar with attendees — live in
[RECIPES.md](RECIPES.md). Read it when you're doing one of those.

## Errors

| Code | Meaning | Response |
|------|---------|----------|
| `FULL_DISK_ACCESS_REQUIRED` | Can't read `~/Library/Mail` or the Calendar store | Walk the user through FDA, then reopen the app |
| `MAIL_STORE_NOT_FOUND` | No Apple Mail store | Add the account in Internet Accounts; let it sync |
| `CALENDAR_STORE_NOT_FOUND` | No Calendar store | Add the account's Calendar in Internet Accounts; let it sync |
| `NOT_FOUND` | Bad/stale id | Re-list, ids can change |
| `VALIDATION_ERROR` | Bad arguments | Re-read `--help` |
| `PLATFORM_UNSUPPORTED` | Not macOS | `apple-mail` is macOS-only |

If a body comes back with `bodyAvailable: false`, the message isn't fully
downloaded locally — tell the user to set the account to download full messages
(Mail → Settings → Accounts), not "recent only".

## When NOT to use

- The user wants to **send, reply, move, delete, flag, or schedule** — `apple-mail` is
  read-only. Use the `outlook-cli` (`olk`) skill, or have the user act in the app.
- Not macOS, or the account isn't synced into Apple Mail/Calendar.
- The user explicitly wants Outlook Web / Graph / cross-tenant org-wide queries.
- Tasks, reminders, notes, contacts, or categories — out of scope.

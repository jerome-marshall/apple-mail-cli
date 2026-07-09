# `apple-mail` recipes

Copy-paste recipes for the common tasks. The rules they rely on (parse with
python, scope with `--mailbox`, drill down with `read`/`get`) live in
[SKILL.md](SKILL.md) — these are just the worked examples.

## Today's mail summary

```bash
TODAY=$(date +%Y-%m-%d)
apple-mail mail list --mailbox Inbox \
  --after "${TODAY}T00:00:00" --before "${TODAY}T23:59:59" \
  --limit 200 --json > /tmp/today.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/today.json"))["data"]
print(f"{d['count']} messages today\n")
for m in d["items"]:
    who = (m.get("from") or {}).get("name") or (m.get("from") or {}).get("address") or "?"
    u = "●" if m.get("isUnread") else " "
    print(f"  {u}  {who[:30]:30}  {(m['subject'] or '')[:70]}")
PY
```

## Find mail from a person about a topic

`search` matches subject, sender, and participants (and preview when present), not
full bodies. For "from X about Y", search broadly then filter client-side, or
`read` the candidates:

```bash
apple-mail mail search "alice" --limit 200 --json > /tmp/alice.json
python3 -c "import json; items=json.load(open('/tmp/alice.json'))['data']['items']; \
  hits=[m for m in items if 'roadmap' in (m.get('subject') or '').lower()]; \
  print(*[f\"{m['id']}  {m['date']}  {m['subject']}\" for m in hits], sep='\n')"
# then, for the body of one hit:
apple-mail mail read 123456 --json
```

## Tomorrow's calendar with full details

```bash
TOMORROW=$(date -v+1d +%Y-%m-%d)
apple-mail cal list --start "${TOMORROW}T00:00:00" --end "${TOMORROW}T23:59:59" --toon
# the list has organizer + attendeeCount + notesPreview; for the full attendee
# list and notes of one event:
apple-mail cal get <eventId> --json
# (eventId is an integer from cal list. For a recurring event, get returns the
#  series master; per-occurrence times come from cal list.)
```

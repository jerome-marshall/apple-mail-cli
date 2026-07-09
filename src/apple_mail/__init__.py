"""apple-mail - read-only, JSON-first CLI over local Apple Mail + Calendar.

The package is deliberately read-only. There is no code path that sends, moves,
deletes, flags, or otherwise mutates mail. Calendar access goes through EventKit,
which is inherently read-only here (we never call save/remove).
"""

__version__ = "0.1.0"

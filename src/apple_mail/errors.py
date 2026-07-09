"""Stable error taxonomy.

Every failure is surfaced as an ``AppleMailError`` carrying a stable ``code`` (safe
to branch on programmatically) and a human-readable ``message``. The CLI turns these
into ``{"ok": false, "error": {"code", "message"}}`` envelopes on stderr.
"""

from __future__ import annotations


# Map of stable codes -> process exit codes. Anything not listed exits 1.
EXIT_CODES: dict[str, int] = {
    "VALIDATION_ERROR": 2,
    "PLATFORM_UNSUPPORTED": 3,
    "NOT_FOUND": 4,
    "FULL_DISK_ACCESS_REQUIRED": 5,
    "MAIL_STORE_NOT_FOUND": 6,
    "CALENDAR_STORE_NOT_FOUND": 6,
    "CALENDAR_STORE_ERROR": 7,
}


class AppleMailError(Exception):
    """A user-surfaceable error with a stable code."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.code, 1)

    def to_dict(self) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        return err


# --- Reusable, fully-formed errors -------------------------------------------

FDA_HINT = (
    "Cursor/your terminal needs Full Disk Access to read ~/Library/Mail. "
    "Grant it once in System Settings > Privacy & Security > Full Disk Access "
    "(toggle the app on), then fully quit and reopen the app. No IT/admin needed."
)


def full_disk_access() -> AppleMailError:
    return AppleMailError("FULL_DISK_ACCESS_REQUIRED", FDA_HINT)


def mail_store_not_found(detail: str = "") -> AppleMailError:
    msg = (
        "Could not locate the Apple Mail store under ~/Library/Mail. "
        "Make sure the work account has been added to Apple Mail (System Settings "
        "> Internet Accounts) and has synced at least once."
    )
    if detail:
        msg = f"{msg} ({detail})"
    return AppleMailError("MAIL_STORE_NOT_FOUND", msg)


def not_found(what: str) -> AppleMailError:
    return AppleMailError("NOT_FOUND", what)


def validation(msg: str) -> AppleMailError:
    return AppleMailError("VALIDATION_ERROR", msg)


def platform_unsupported() -> AppleMailError:
    return AppleMailError(
        "PLATFORM_UNSUPPORTED",
        "apple-mail only works on macOS; it reads the local Apple Mail and Calendar stores.",
    )

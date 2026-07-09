from pathlib import Path

from apple_mail.mail.emlx import parse_emlx


def _write_emlx(path: Path, rfc822: bytes, trailer: bytes = b"") -> Path:
    framed = f"{len(rfc822)}\n".encode() + rfc822 + trailer
    path.write_bytes(framed)
    return path


PLAIN = (
    b"From: Alice Example <alice@example.com>\r\n"
    b"To: Bob <bob@example.com>, carol@x.com\r\n"
    b"Cc: Dan <dan@x.com>\r\n"
    b"Subject: Hello there\r\n"
    b"Date: Wed, 24 Jun 2026 10:00:00 +0530\r\n"
    b"Message-ID: <abc123@example.com>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"This is the body.\r\nSecond line.\r\n"
)

MULTIPART = (
    b"From: Eve <eve@example.com>\r\n"
    b"To: Frank <frank@example.com>\r\n"
    b"Subject: Mixed\r\n"
    b"Date: Wed, 24 Jun 2026 11:00:00 +0530\r\n"
    b'Content-Type: multipart/alternative; boundary="BB"\r\n'
    b"\r\n"
    b"--BB\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"plain version\r\n"
    b"--BB\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<p>html version</p>\r\n"
    b"--BB--\r\n"
)


def test_parse_plain(tmp_path):
    p = _write_emlx(tmp_path / "1.emlx", PLAIN, b"<plist></plist>")
    msg = parse_emlx(p)
    assert msg["subject"] == "Hello there"
    assert msg["from"] == {"name": "Alice Example", "address": "alice@example.com"}
    assert {"name": "Bob", "address": "bob@example.com"} in msg["to"]
    assert {"name": None, "address": "carol@x.com"} in msg["to"]
    assert msg["cc"] == [{"name": "Dan", "address": "dan@x.com"}]
    assert msg["messageId"] == "<abc123@example.com>"
    assert "This is the body." in msg["body"]["text"]
    assert msg["body"]["html"] is None


def test_parse_multipart(tmp_path):
    p = _write_emlx(tmp_path / "2.emlx", MULTIPART)
    msg = parse_emlx(p)
    assert "plain version" in msg["body"]["text"]
    assert "html version" in msg["body"]["html"]


def test_bad_prefix(tmp_path):
    p = tmp_path / "bad.emlx"
    p.write_bytes(b"not-a-number\r\nstuff")
    try:
        parse_emlx(p)
    except ValueError:
        return
    raise AssertionError("expected ValueError for bad length prefix")

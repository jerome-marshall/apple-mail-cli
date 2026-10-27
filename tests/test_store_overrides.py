import sqlite3
import sys

import pytest

from apple_mail.cal import store as cal_store
from apple_mail.errors import AppleMailError
from apple_mail.mail import store as mail_store


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv(mail_store.MAIL_ROOT_ENV, raising=False)
    monkeypatch.delenv(cal_store.CAL_DIR_ENV, raising=False)


def test_mail_off_macos_without_override_is_unsupported(linux):
    with pytest.raises(AppleMailError) as exc:
        mail_store.locate_version_dir()
    assert exc.value.code == "PLATFORM_UNSUPPORTED"


def test_mail_root_override_picks_highest_version(linux, monkeypatch, tmp_path):
    for name in ("V9", "V10", "notes"):
        (tmp_path / name).mkdir()
    monkeypatch.setenv(mail_store.MAIL_ROOT_ENV, str(tmp_path))
    assert mail_store.locate_version_dir() == tmp_path / "V10"


def test_mail_root_override_missing_dir(linux, monkeypatch, tmp_path):
    monkeypatch.setenv(mail_store.MAIL_ROOT_ENV, str(tmp_path / "absent"))
    with pytest.raises(AppleMailError) as exc:
        mail_store.locate_version_dir()
    assert exc.value.code == "MAIL_STORE_NOT_FOUND"


def test_calendar_off_macos_without_override_is_unsupported(linux):
    assert cal_store.health()["code"] == "PLATFORM_UNSUPPORTED"


def test_calendar_dir_override_reads_snapshot(linux, monkeypatch, tmp_path):
    conn = sqlite3.connect(tmp_path / "Calendar.sqlitedb")
    conn.execute("create table CalendarItem (ROWID integer primary key)")
    conn.executemany("insert into CalendarItem default values", [()] * 3)
    conn.commit()
    conn.close()
    monkeypatch.setenv(cal_store.CAL_DIR_ENV, str(tmp_path))
    health = cal_store.health()
    assert health == {"ok": True, "store": str(tmp_path / "Calendar.sqlitedb"), "itemCount": 3}

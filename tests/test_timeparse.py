from datetime import timezone

from aml.timeparse import parse_dt, store_to_unix, to_epoch


def test_naked_is_local():
    dt = parse_dt("2026-06-24T09:00:00")
    assert dt.tzinfo is not None  # local tz attached
    assert dt.utcoffset() == __import__("datetime").datetime.now().astimezone().utcoffset()


def test_z_is_utc():
    dt = parse_dt("2026-06-24T09:00:00Z")
    assert dt.utcoffset() == timezone.utc.utcoffset(None)


def test_explicit_offset():
    dt = parse_dt("2026-06-24T09:00:00+05:30")
    assert dt.utcoffset().total_seconds() == 5.5 * 3600


def test_bare_date_is_local_midnight():
    dt = parse_dt("2026-06-24")
    assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)
    assert dt.tzinfo is not None


def test_to_epoch_matches_utc():
    assert to_epoch("1970-01-01T00:00:00Z") == 0.0


def test_store_to_unix_cf_offset():
    # A CoreFoundation timestamp (< 1e9) gets the 2001 epoch added.
    cf = 700_000_000  # ~2023 in CF time
    assert store_to_unix(cf) == cf + 978307200


def test_store_to_unix_passthrough():
    unix = 1_750_000_000  # already Unix seconds
    assert store_to_unix(unix) == unix

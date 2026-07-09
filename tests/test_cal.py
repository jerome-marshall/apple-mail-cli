from aml.cal.store import _CF_EPOCH, _preview, cf_iso


def test_cf_iso_epoch_is_2001():
    # CoreFoundation absolute time 0 == 2001-01-01 UTC.
    iso = cf_iso(0)
    assert iso is not None
    assert iso.startswith("2001-01-01")


def test_cf_iso_none():
    assert cf_iso(None) is None


def test_cf_epoch_constant():
    assert _CF_EPOCH == 978307200


def test_preview_trims_and_collapses_whitespace():
    assert _preview("  hello   world\n\nfoo ") == "hello world foo"


def test_preview_truncates():
    out = _preview("x" * 500, n=50)
    assert len(out) == 50
    assert out.endswith("\u2026")


def test_preview_empty():
    assert _preview("") is None
    assert _preview(None) is None

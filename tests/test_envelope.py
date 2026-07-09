import io
import json

from apple_mail.envelope import emit_error, emit_success, list_payload
from apple_mail.errors import AppleMailError


class _Buf(io.StringIO):
    def isatty(self):
        return False


def test_success_compact_json():
    buf = _Buf()
    emit_success({"name": "apple-mail"}, "json", stream=buf)
    obj = json.loads(buf.getvalue())
    assert obj == {"ok": True, "data": {"name": "apple-mail"}}


def test_list_payload_shape():
    payload = list_payload([1, 2, 3], has_more=True)
    assert payload == {"items": [1, 2, 3], "count": 3, "hasMore": True}


def test_ndjson_lists_emit_one_item_per_line():
    buf = _Buf()
    emit_success(list_payload([{"id": 1}, {"id": 2}]), "ndjson", stream=buf)
    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert lines == [{"id": 1}, {"id": 2}]


def test_error_envelope():
    buf = _Buf()
    emit_error(AppleMailError("NOT_FOUND", "nope"), "json", stream=buf)
    obj = json.loads(buf.getvalue())
    assert obj == {"ok": False, "error": {"code": "NOT_FOUND", "message": "nope"}}

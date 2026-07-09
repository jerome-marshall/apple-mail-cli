import io
import json

from aml.envelope import emit_error, emit_success, list_payload
from aml.errors import AmlError


class _Buf(io.StringIO):
    def isatty(self):
        return False


def test_success_compact_json():
    buf = _Buf()
    emit_success({"name": "aml"}, "json", stream=buf)
    obj = json.loads(buf.getvalue())
    assert obj == {"ok": True, "data": {"name": "aml"}}


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
    emit_error(AmlError("NOT_FOUND", "nope"), "json", stream=buf)
    obj = json.loads(buf.getvalue())
    assert obj == {"ok": False, "error": {"code": "NOT_FOUND", "message": "nope"}}

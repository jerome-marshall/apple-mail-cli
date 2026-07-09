from apple_mail.toon import encode


def test_scalar_object():
    out = encode({"ok": True, "data": {"name": "apple-mail", "version": "0.1.0"}})
    assert "ok: true" in out
    assert "name: apple-mail" in out
    assert "version: 0.1.0" in out


def test_uniform_object_array_is_tabular():
    data = {
        "items": [
            {"id": 1, "subject": "a"},
            {"id": 2, "subject": "b"},
        ]
    }
    out = encode(data)
    assert "items[2]{id,subject}:" in out
    lines = out.strip().splitlines()
    assert any(line.strip() == "1,a" for line in lines)
    assert any(line.strip() == "2,b" for line in lines)


def test_scalar_array_inline():
    out = encode({"tags": ["x", "y", "z"]})
    assert "tags[3]: x,y,z" in out


def test_quoting_special_chars():
    out = encode({"subject": "Hello, world"})
    assert '"Hello, world"' in out


def test_irregular_array_block_fallback():
    data = {"items": [{"id": 1, "to": ["a", "b"]}]}
    out = encode(data)
    assert "items[1]:" in out
    assert "-" in out

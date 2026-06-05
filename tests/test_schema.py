from flowleaf import schema


SCHEMA = {
    "type": "object",
    "required": ["title", "severity"],
    "properties": {"title": {"type": "string"}, "severity": {"type": "string"}},
}


def test_parse_clean_json():
    ok, val, err = schema.parse('{"title": "x", "severity": "high"}', SCHEMA)
    assert ok and val["title"] == "x" and err is None


def test_parse_fenced_json():
    ok, val, err = schema.parse('```json\n{"title":"y","severity":"low"}\n```', SCHEMA)
    assert ok and val["severity"] == "low"


def test_parse_embedded_json():
    ok, val, err = schema.parse('here you go: {"title":"z","severity":"med"} done', SCHEMA)
    assert ok and val["title"] == "z"


def test_missing_required_fails():
    ok, val, err = schema.parse('{"title": "only"}', SCHEMA)
    assert not ok and "severity" in err


def test_no_json_fails():
    ok, val, err = schema.parse("no json here", SCHEMA)
    assert not ok and val is None


def test_repair_prompt_includes_error():
    p = schema.repair_prompt("orig", SCHEMA, '{"bad":1}', "missing required keys: ['title']")
    assert "FAILED" in p and "title" in p

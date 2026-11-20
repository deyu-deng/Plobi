"""Schema-shape tests for the built-in memory tool.

The memory tool previously used ``allOf: [{if: ..., then: {required: ...}}]``
at the top level of ``parameters`` to hint per-action required fields.  That
form was:

  1. Ignored by every provider (Chat Completions doesn't honour ``if/then``
     on function schemas), so it never actually enforced anything.
  2. **Rejected outright by strict backends** — OpenAI's Codex endpoint
     (``chatgpt.com/backend-api/codex``, gpt-5.x) returns
     ``Invalid schema for function 'memory': schema must have type 'object'
     and not have 'oneOf'/'anyOf'/'allOf'/'enum'/'not' at the top level``.

We now rely on the runtime handler (``memory_tool()`` in ``tools/memory_tool.py``)
to validate required fields per action and return actionable error messages.
These tests guard the schema against regressing back to a shape strict
backends reject.
"""

import json

from tools.memory_tool import MEMORY_SCHEMA


_FORBIDDEN_TOP_LEVEL_KEYS = ("allOf", "anyOf", "oneOf", "enum", "not")


def test_memory_schema_has_no_forbidden_top_level_combinators():
    """OpenAI's Codex backend rejects these at the top level of parameters."""
    params = MEMORY_SCHEMA["parameters"]
    for key in _FORBIDDEN_TOP_LEVEL_KEYS:
        assert key not in params, (
            f"top-level {key!r} in memory tool parameters will break the "
            "Codex backend (chatgpt.com/backend-api/codex). Per-action "
            "required-field checks belong in the runtime handler, not the schema."
        )


def test_memory_schema_is_well_formed():
    params = MEMORY_SCHEMA["parameters"]
    assert params["type"] == "object"
    # Only ``target`` is universally required: ``action`` belongs to the
    # single-op shape and is omitted when the batch ``operations`` array is used.
    assert params["required"] == ["target"]
    # Nested ``enum`` on property values is fine — only top-level is forbidden.
    # 裁定 185：action 只有三个原子动词，没有"业务分支"那种参数。
    assert params["properties"]["action"]["enum"] == ["add", "replace", "remove"]
    # 裁定 181：四个作用域是四个数据位置（global 的规则是"要人批"，不是新动作）。
    assert set(params["properties"]["target"]["enum"]) == {
        "memory", "user", "status", "global"}
    assert "source" in params["properties"], "出处指针没了，2200 字撑不住三份"
    # Batch shape is exposed and its items reuse the same actions.
    assert params["properties"]["operations"]["type"] == "array"
    assert params["properties"]["operations"]["items"]["properties"]["action"]["enum"] == ["add", "replace", "remove"]


def test_memory_schema_is_json_serializable():
    json.dumps(MEMORY_SCHEMA)


def test_memory_write_exposes_surgical_edits_and_does_not_require_parts():
    """`edits` 与 `parts` 是同一格的两条路，所以 schema 不能把 `parts` 写成必填。

    写成 `required: ["parts"]` 的后果很具体：只带 `edits` 的那一笔在严格后端上
    直接 400 —— 而那正是「改一条、别的字节不动」唯一的路。
    """
    from tools.memory_tool import MEMORY_WRITE_SCHEMA

    params = MEMORY_WRITE_SCHEMA["parameters"]
    for key in _FORBIDDEN_TOP_LEVEL_KEYS:
        assert key not in params, f"top-level {key!r} breaks strict backends"

    props = params["properties"]
    assert {"scope", "edits", "parts", "source"} == set(props)
    assert "required" not in params, "edits-only 调用会被 required 挡在门外"
    assert props["edits"]["type"] == "array"
    item = props["edits"]["items"]
    assert item["type"] == "object"
    assert item["properties"]["op"]["enum"] == ["add", "replace", "remove"]
    assert {"old_text", "content"} <= set(item["properties"])

"""Validate kanban_create tool's required field enforcement.

11-10 更正一格形状（这一档从 2026-10-31 `72f19348`（WP-KANBAN-BODY）起就是红的）：
那一笔同时落了「实现」与「测试」，而测试断言的是 ``parsed.get("ok") is False``——
可实现走的是全仓统一的错误序列化 ``tools.registry.tool_error``，它的形状是
``{"error": "..."}``（**没有** ``ok`` 键）。于是这条断言从出生就红。

这里改的是**测试**，不是产品：
· ``tool_error`` 是整个工具面唯一的错误形状（它的 docstring 里就写着
  ``tool_error("file not found")`` → ``{"error": "file not found"}``）；
  给看板这一族另立一个 ``{"ok": false, "error": …}`` 的形状，就是第二个形状——
  而「第二个形状」正是这个仓反复吃亏的那类事。
· 这一档的本意是**钉住「必须有 body」**（文件名与三段 docstring 都这么说），
  ``ok`` 键只是顺带写下的断言细节。
"""

import json as _json
from tools.kanban_tools import _handle_create


def _rejected(result):
    """这一族的错误回包：``{"error": "..."}``（产品唯一那个错误形状）。"""
    parsed = _json.loads(result)
    assert isinstance(parsed, dict), f"Expected a JSON object, got: {result}"
    assert "error" in parsed, (
        f"kanban_create should reject this call with an error payload, but got: {result}"
    )
    return parsed


def test_body_is_now_required():
    """Kanban tasks without body should be rejected by explicit validation."""
    # Title + assignee only — body missing
    args = {
        "title": "Test task",
        "assignee": "researcher-a",
        # No body!
    }

    result = _handle_create(args)
    assert isinstance(result, str), f"Expected string result, got {type(result)}"

    parsed = _rejected(result)
    error = parsed.get("error", "")
    assert "body" in error.lower(), (
        f"Error message should mention 'body' as required: {error}"
    )
    # The new explicit error message should guide the model better
    assert "don't just put a title" in error.lower(), (
        f"Error message should explicitly warn against using only title: {error}"
    )


def test_empty_body_rejected():
    """Whitespace-only body should also be rejected."""
    args = {
        "title": "Test task",
        "assignee": "researcher-a",
        "body": "   ",  # Only whitespace
    }

    result = _handle_create(args)
    parsed = _rejected(result)
    assert "body" in parsed.get("error", "").lower()


def test_title_assignee_still_required():
    """Ensure title and assignee remain required.

    产品的校验顺序是 title → assignee → body（`_handle_create` 里三道闸挨着），
    所以**一次只报第一道缺的那一个**。这一条因此拆成两段钉：只给 body ⇒ 报
    title；给了 title 少了 assignee ⇒ 报 assignee。两段合起来才是
    「title 与 assignee 都仍然是必填」这句断言。
    """
    # Only body — no title
    parsed = _rejected(_handle_create({"body": "Detailed spec here..."}))
    error = parsed.get("error", "").lower()
    assert "title" in error, f"Should complain about missing title: {error}"

    # Title present, assignee missing
    parsed = _rejected(
        _handle_create({"title": "Test task", "body": "Detailed spec here..."})
    )
    error = parsed.get("error", "").lower()
    assert "assignee" in error, f"Should complain about missing assignee: {error}"


def test_all_three_fields_accepted_by_validation():
    """Valid call with title + assignee + body should pass field validation."""
    args = {
        "title": "Complete task",
        "assignee": "reviewer",
        "body": "Full specification with acceptance criteria.\n\nLinks: https://example.com",
    }
    
    # This will fail at DB level because we're not mocking the board,
    # but it should PASS the required-field check first.
    result = _handle_create(args)
    
    # We expect either:
    # - OK from successful creation
    # - An error about missing board/DB connection
    # - NOT a "field required" error
    parsed = _json.loads(result)
    
    if parsed.get("ok"):
        return  # Success! Task created.
    
    # If failed, make sure it's not because of missing required fields
    error = parsed.get("error", "").lower()
    for field in ("title", "assignee", "body"):
        assert field not in error, (
            f"Task failed with '{field}' missing, but we provided all three: {error}"
        )

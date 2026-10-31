"""Validate kanban_create tool's required field enforcement."""

import json as _json
from tools.kanban_tools import _handle_create


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
    
    parsed = _json.loads(result)
    assert parsed.get("ok") is False, (
        f"kanban_create should reject calls without 'body', but got: {result}"
    )
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
    parsed = _json.loads(result)
    assert parsed.get("ok") is False
    assert "body" in parsed.get("error", "").lower()


def test_title_assignee_still_required():
    """Ensure title and assignee remain required."""
    # Only body — no title or assignee
    args = {
        "body": "Detailed spec here...",
    }
    
    result = _handle_create(args)
    parsed = _json.loads(result)
    assert parsed.get("ok") is False
    
    error = parsed.get("error", "").lower()
    assert "title" in error, "Should complain about missing title"
    assert "assignee" in error, "Should complain about missing assignee"


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

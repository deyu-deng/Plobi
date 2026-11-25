"""The schema's option lists must offer the value the product actually uses.

``WP-SCREEN-OPTIONS-REGISTRY`` — the screen used to read its own copy of
every list, and the two copies drifted: ``approvals.mode`` offered the
retired ``ask``/``yolo``/``deny`` in the backend while the desktop offered
``manual``/``smart``/``off``; ``delegation.reasoning_effort`` was missing
``minimal``/``xhigh``/``max`` on the backend and ``max`` on the desktop.

The product's enumerations now live in the backend schema only, and the
desktop renders them.  These tests pin the relation that matters:
**whatever the product defaults to must be among the options the screen
offers** — the failure that a hand-written list produces is always a
default the screen cannot show.

They are relationship tests, not snapshots: a new option or a new default
does not fail them, a default the screen cannot select does.
"""

import pytest


def _default_for(key: str, config: dict):
    """The default value for a dot-path key, or None when there is none."""
    cur = config
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


@pytest.fixture(scope="module")
def schema():
    from plobi_cli.web_server import CONFIG_SCHEMA

    return CONFIG_SCHEMA


def test_every_select_field_offers_the_value_the_product_defaults_to(schema):
    from plobi_cli.config import DEFAULT_CONFIG

    offenders = []
    for key, entry in schema.items():
        if entry.get("type") != "select":
            continue
        value = _default_for(key, DEFAULT_CONFIG)
        if not isinstance(value, str) or not value:
            # An empty default is the "not set" state, not a missing option.
            continue
        options = entry.get("options") or []
        if value not in [str(o) for o in options]:
            offenders.append((key, value, options))

    assert offenders == [], (
        "these fields default to a value their own option list does not offer: "
        f"{offenders}"
    )


def test_every_option_the_screen_offers_is_one_the_code_accepts(schema):
    """The strongest relation available: ask the code about each option.

    A hand-written list drifts into offering values the product never had
    (``approvals.mode`` offered the retired ``ask``/``yolo``/``deny``;
    ``terminal.modal_mode`` offered ``sandbox``/``function``, which the code
    does not know, while the value it defaults to was not offered at all).
    Feeding each option back through the code that reads it settles the
    question without importing a private constant.
    """
    from tools.approval import _normalize_approval_mode
    from tools.tool_backend_helpers import coerce_modal_mode

    for option in schema["approvals.mode"]["options"]:
        assert _normalize_approval_mode(option) == option, (
            f"the screen offers the approval mode {option!r}, the code does not accept it"
        )

    for option in schema["terminal.modal_mode"]["options"]:
        assert coerce_modal_mode(option) == option, (
            f"the screen offers the modal mode {option!r}, the code does not accept it"
        )


def test_reasoning_efforts_come_from_the_constant(schema):
    from plobi_constants import VALID_REASONING_EFFORTS

    options = schema["delegation.reasoning_effort"]["options"]
    assert set(VALID_REASONING_EFFORTS) <= set(options)


def test_the_retired_approval_modes_are_gone(schema):
    """A retired mode in the list is how the screen ended up offering ghosts."""
    options = schema["approvals.mode"]["options"]
    assert not ({"ask", "yolo", "deny"} & set(options))

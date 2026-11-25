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


def test_the_memory_provider_row_exists(schema):
    """外脑（官方外脑）那一行**必须在 schema 里**。

    2026-11-25 现读（`WP-EXOCORTEX-ENTRY`）：``memory.provider`` 一直被
    ``_build_schema_from_config`` 跳过（它当年是内部键）⇒ 桌面设置屏上
    **根本没有「接哪个外部脑」这一行** ⇒ 连带的连接流程与各家字段编辑器都
    够不着 ⇒ 后端那 8+ 家外脑在屏上一个都选不到。
    """
    entry = schema.get("memory.provider")

    assert entry is not None, "屏上没有「接哪个外部脑」那一行"
    assert entry.get("type") == "select"
    assert entry.get("category") == "memory"


def test_the_memory_provider_options_are_the_registered_providers():
    """那一行的选项＝**后端真有的那几家**（不是手抄的三项）。

    同一份机制：tts／stt 的 provider 也是每次请求现算的（`options_source`
    = ``registry``）。官方脑**不在这一列里**（``WP-MIND-COEXIST``：它是内核
    一等公民，有自己那个常驻开关 ``memory.mind_enabled``）。
    """
    import asyncio

    from plobi_cli.web_server import get_schema
    from plugins.memory import discover_memory_providers

    payload = asyncio.run(get_schema())
    entry = payload["fields"].get("memory.provider")

    assert entry is not None
    assert entry.get("options_source") == "registry"

    options = [str(o) for o in entry.get("options") or []]
    discovered = [name for name, _desc, _available in discover_memory_providers()]

    # 屏上给的就是真有的那几家（插件装上／卸下都跟着变）。
    assert set(discovered) <= set(options)
    # 「关掉」那一行在最前（配置里的默认值就是空串）。
    assert options[0] == ""
    # 官方脑不挤在这一列里。
    assert "mind" not in options
    # 人话标签也由后端给（插件自己声明的 `label:`）。
    labels = entry.get("option_labels") or {}
    assert labels.get("hindsight") == "Hindsight"

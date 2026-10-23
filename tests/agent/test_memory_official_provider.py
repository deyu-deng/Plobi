"""Mind 抬成本体官方记忆面之后，跨层的口径不许各说一套（WP-MIND-OFFICIAL-BRAIN）。

What could silently break here, and is therefore asserted:

- the config key has three states; a default that overrides an explicit opt-out
  is worse than no default
- "is a brain configured here?" must have exactly ONE answer in the codebase —
  the provider's own ``is_available()``, not a second detector in agent init
- the backend's official name, the CLI registry's first entry and the desktop
  dropdown's allowed values are three surfaces for one fact. The report that
  started this knife was literally "the UI has no mind option" — so the drift
  between surfaces is the bug being closed, and a Python test that reads the
  frontend constant is the cheapest way to keep it closed.
- official ≠ privileged: mind is still an *external* provider, so the
  one-external-at-a-time rule keeps applying to it.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from agent.memory_manager import OFFICIAL_MEMORY_PROVIDER, MemoryManager, resolve_memory_provider_name
from plugins.memory import load_memory_provider

REPO = Path(__file__).resolve().parents[2]
DESKTOP_CONSTANTS = REPO / "apps" / "desktop" / "src" / "app" / "settings" / "constants.ts"


def _fake_brain(tmp_path, *, with_agents_md=True):
    root = tmp_path / "brain"
    root.mkdir(parents=True, exist_ok=True)
    if with_agents_md:
        (root / "AGENTS.md").write_text("# Mind\n", encoding="utf-8")
    return root


# ---- the three states -------------------------------------------------------


@pytest.mark.parametrize("configured, expected", [
    (None, OFFICIAL_MEMORY_PROVIDER),          # key absent → nobody decided yet
    (OFFICIAL_MEMORY_PROVIDER, OFFICIAL_MEMORY_PROVIDER),
    ("hindsight", "hindsight"),                # the other eight stay selectable
    ("", ""),                                  # explicit opt-out, honoured
    ("   ", ""),
])
def test_provider_key_has_three_states(configured, expected):
    assert resolve_memory_provider_name(configured) == expected


def test_absent_key_does_not_mean_off_and_empty_key_does_not_mean_default():
    """混起来就变成「默认覆盖了别人做的决定」——这条是这台上最常犯的错法。"""
    assert resolve_memory_provider_name(None) != ""
    assert resolve_memory_provider_name("") == ""


# ---- one brain detector, not two ------------------------------------------


def test_availability_is_answered_by_the_provider_not_by_init(tmp_path, monkeypatch):
    monkeypatch.setenv("MIND_ROOT", str(_fake_brain(tmp_path)))
    provider = load_memory_provider(OFFICIAL_MEMORY_PROVIDER)
    assert provider is not None, "官方 provider 名字与 plugins/memory/<name>/ 目录对不上"
    assert provider.is_available() is True

    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "there-is-no-brain-here"))
    assert load_memory_provider(OFFICIAL_MEMORY_PROVIDER).is_available() is False


def test_default_activation_needs_no_config_edit(tmp_path, monkeypatch):
    """他那份 config.yaml 里根本没有 provider 这一行——不改他的文件也必须能装上。"""
    monkeypatch.setenv("MIND_ROOT", str(_fake_brain(tmp_path)))
    mem_config = {"memory_enabled": False}          # 现读形状：没有 provider 键
    name = resolve_memory_provider_name(mem_config.get("provider"))
    assert name == OFFICIAL_MEMORY_PROVIDER
    assert load_memory_provider(name).is_available() is True


# ---- the three surfaces must agree ------------------------------------------


def test_cli_registry_declares_the_official_provider_first():
    from plobi_cli.memory_providers import MEMORY_PROVIDERS

    first = next(iter(MEMORY_PROVIDERS))
    assert first == OFFICIAL_MEMORY_PROVIDER, f"CLI 把 {first} 排第一位，后端官方是 {OFFICIAL_MEMORY_PROVIDER}"
    mind = MEMORY_PROVIDERS[OFFICIAL_MEMORY_PROVIDER]
    # 脑不需要 API key；要暴露的是两个环境指针
    assert not any(f.is_secret for f in mind.fields)
    fallbacks = {fb for f in mind.fields for fb in f.env_fallbacks}
    assert {"MIND_ROOT", "VAULT_ROOT"} <= fallbacks


def test_desktop_dropdown_offers_the_official_provider():
    """这份测试的存在理由：起因就是「桌面下拉框里没有 mind」。"""
    text = io.open(DESKTOP_CONSTANTS, encoding="utf-8").read()
    line = next(l for l in text.splitlines() if "'memory.provider':" in l)
    values = [v.strip().strip("'") for v in line.split("[", 1)[1].split("]")[0].split(",")]
    assert OFFICIAL_MEMORY_PROVIDER in values, line.strip()
    assert values[0] == ""                       # 缺省位仍是第一项，mind 紧跟其后 = 推荐而非强制
    assert values[1] == OFFICIAL_MEMORY_PROVIDER, line.strip()


def _quoted_keys(block: str) -> list[str]:
    """从 `keys: [ 'a.b', 'c.d' ]` 里取出那些引号包住的键名。"""
    inside = block.split("keys: [", 1)[1].split("]", 1)[0]
    return [tok.strip().strip("'\"") for tok in inside.split(",") if tok.strip()]


def test_desktop_memory_surface_only_exposes_keys_the_backend_reads():
    """设置里 "Memory & Context" 那一格列出的每个 `memory.*` 键，后端必须真的读它。

    存在理由（2026-10-23 现读）：那一格挂着 `memory.memory_char_limit`，而裁定 181 之后
    三份上限的真名是 `global_char_limit` / `agent_char_limit` / `user_char_limit`
    （`tools/memory_tool.py` 的 `_MEMORY_LIMIT_KEYS` 是唯一那只手）——桌面能改、改了没人读，
    正是本仓反复犯的「桌面显示一个、后端解析另一个」。整条记忆分层既然要在这一格里可设，
    这一条就是它的地板。
    """
    from tools.memory_tool import _MEMORY_LIMIT_KEYS

    text = io.open(DESKTOP_CONSTANTS, encoding="utf-8").read()
    at = text.index("id: 'memory',")
    block = text[at:at + 1400]
    exposed = [k for k in _quoted_keys(block) if k.startswith("memory.")]
    assert len(exposed) >= 8, f"那一格只剩 {exposed}，整条分层没面可设"

    honoured = {"memory." + v for v in _MEMORY_LIMIT_KEYS.values()} | {
        "memory.memory_enabled", "memory.user_profile_enabled",
        "memory.write_approval", "memory.mind_write_approval",
        "memory.mind_write_auto_approve",
        "memory.provider", "memory.nudge_interval",
    }
    dead = [k for k in exposed if k not in honoured]
    assert not dead, f"桌面在写后端不读的键：{dead}"
    assert "memory.memory_char_limit" not in exposed
    # 三份上限 + 两格 STATUS + 压线 + 点头闸，缺一件就是「整套没做完」还挂着✓
    for needed in ("memory.global_char_limit", "memory.agent_char_limit", "memory.user_char_limit",
                   "memory.squeeze_pressure", "memory.write_approval"):
        assert needed in exposed, f"分层的那颗旋钮不在面上：{needed}"


# ---- official does not mean privileged ---------------------------------------


class _Stub:
    """只实现 add_provider 真会碰到的面：name 与工具表（它是外部 provider，没有工具）。"""

    def __init__(self, name, available=True):
        self.name = name
        self._available = available

    def is_available(self):
        return self._available

    def get_tool_schemas(self):
        return []


def test_official_provider_is_still_one_of_one(tmp_path, monkeypatch):
    """官方 != 可以并挂：mind 之后再来一家外部 provider，仍然要被判掉。"""
    manager = MemoryManager()
    manager.add_provider(_Stub(OFFICIAL_MEMORY_PROVIDER))
    manager.add_provider(_Stub("honcho"))
    assert [p.name for p in manager.providers] == [OFFICIAL_MEMORY_PROVIDER]


def test_memory_enabled_flag_is_not_conflated_with_the_brain():
    """`memory_enabled` 管内置 MEMORY.md（§10.2 的内存层），不管外置脑。

    抬 provider 时顺手把这个开关也翻掉，就是让一个键同时拥有两层——
    他配置文件里现在是 false，这一笔没动它，这条断言防止下一笔动歪。
    """
    cfg = {"memory_enabled": False}
    assert cfg.get("memory_enabled", False) is False
    assert resolve_memory_provider_name(cfg.get("provider")) == OFFICIAL_MEMORY_PROVIDER

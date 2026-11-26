"""Mind 抬成本体官方记忆面之后，跨层的口径不许各说一套（WP-MIND-OFFICIAL-BRAIN）。

What could silently break here, and is therefore asserted:

- 两个键管两件正交的事：``memory.mind_enabled`` 管 Mind 在不在，``memory.provider``
  只指外部服务。把这两个决定压回一个单选，就是「选了 honcho ＝ 官方脑停用」那条腐烂
  （``WP-MIND-COEXIST`` 拆掉的就是它）。
- "is a brain configured here?" must have exactly ONE answer in the codebase —
  the provider's own ``is_available()``, not a second detector in agent init
- the backend's official name, the CLI registry's first entry and the desktop
  dropdown's allowed values are three surfaces for one fact. The report that
  started this knife was literally "the UI has no mind option" — so the drift
  between surfaces is the bug being closed, and a Python test that reads the
  frontend constant is the cheapest way to keep it closed. **11-17 之后这条判据反过来了**：
  Mind 不再出现在那个下拉里（它不是外部服务），改成由同一屏 Mind 组自己的开关管。
- official ≠ privileged but official ≠ external either: 官方脑**不占**第三方那一个
  槽位，两家可以同时在册；被判掉的只有第二家**第三方**。
- the brain is not a plugin: it must come from the core module, never from a
  directory under plugins/memory/.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from agent.memory_manager import (
    OFFICIAL_MEMORY_PROVIDER,
    MemoryManager,
    resolve_external_provider_name,
    resolve_mind_enabled,
)
from plugins.memory import load_memory_provider

REPO = Path(__file__).resolve().parents[2]
DESKTOP_CONSTANTS = REPO / "apps" / "desktop" / "src" / "app" / "settings" / "constants.ts"


def _fake_brain(tmp_path, *, with_agents_md=True):
    root = tmp_path / "brain"
    root.mkdir(parents=True, exist_ok=True)
    if with_agents_md:
        (root / "AGENTS.md").write_text("# Mind\n", encoding="utf-8")
    return root


# ---- 两个键管两件正交的事（WP-MIND-COEXIST）---------------------------------


@pytest.mark.parametrize("configured, expected", [
    (None, ""),                                # 缺键 → 不接外部服务
    (OFFICIAL_MEMORY_PROVIDER, ""),            # 老配置说的「官方脑」不是一家外部服务
    ("hindsight", "hindsight"),                # 那八家照旧可选
    ("", ""),                                  # 显式退订
    ("   ", ""),
])
def test_provider_key_names_an_external_service_only(configured, expected):
    assert resolve_external_provider_name(configured) == expected


def test_the_brain_switch_does_not_depend_on_the_provider_key():
    """Mind 在不在由自己的开关说，与「要接哪家外部」无关——这就是这一刀本身。"""
    assert resolve_mind_enabled(None) is True          # 缺键＝常驻
    assert resolve_mind_enabled(True) is True
    assert resolve_mind_enabled(False) is False
    # 老配置把两个决定压在一个键上，现在两者互不影响
    assert resolve_mind_enabled(None) is True
    assert resolve_external_provider_name("honcho") == "honcho"


# ---- one brain detector, not two ------------------------------------------


def test_availability_is_answered_by_the_provider_not_by_init(tmp_path, monkeypatch):
    monkeypatch.setenv("MIND_ROOT", str(_fake_brain(tmp_path)))
    provider = load_memory_provider(OFFICIAL_MEMORY_PROVIDER)
    assert provider is not None, "官方 provider 从内核加载失败（它已不是插件目录）"
    assert provider.is_available() is True

    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "there-is-no-brain-here"))
    assert load_memory_provider(OFFICIAL_MEMORY_PROVIDER).is_available() is False


def test_official_brain_comes_from_core_not_a_plugin_dir(tmp_path, monkeypatch):
    """「它是官方记忆脑，不是插件」必须是可回归的判据，不能只靠注释自觉。"""
    monkeypatch.setenv("MIND_ROOT", str(_fake_brain(tmp_path)))
    brain = load_memory_provider(OFFICIAL_MEMORY_PROVIDER)
    assert brain is not None
    assert type(brain).__module__ == "plobi.mind.provider", type(brain).__module__
    assert not type(brain).__module__.startswith("plugins.memory.")


def test_default_activation_needs_no_config_edit(tmp_path, monkeypatch):
    """他那份 config.yaml 里根本没有 provider 这一行——不改他的文件也必须能装上。"""
    monkeypatch.setenv("MIND_ROOT", str(_fake_brain(tmp_path)))
    mem_config = {"memory_enabled": False}          # 现读形状：没有 provider / mind_enabled 键
    assert resolve_mind_enabled(mem_config.get("mind_enabled")) is True
    assert load_memory_provider(OFFICIAL_MEMORY_PROVIDER).is_available() is True


# ---- the three surfaces must agree ------------------------------------------


def test_cli_registry_declares_the_official_provider_first():
    from plobi_cli.memory_providers import MEMORY_PROVIDERS

    first = next(iter(MEMORY_PROVIDERS))
    assert first == OFFICIAL_MEMORY_PROVIDER, f"CLI 把 {first} 排第一位，后端官方是 {OFFICIAL_MEMORY_PROVIDER}"
    mind = MEMORY_PROVIDERS[OFFICIAL_MEMORY_PROVIDER]
    # 脑不需要 API key；要暴露的是两个环境指针
    assert not any(f.is_secret for f in mind.fields)
    fallbacks = {fb for f in mind.fields for fb in f.env_fallbacks}
    assert {"MIND_ROOT", "HUMAN_ROOT"} <= fallbacks


def test_desktop_dropdown_does_not_offer_the_official_provider():
    """Mind 不是一家外部服务，所以不该出现在「外部记忆提供商」那个下拉里。

    这条测试的前身钉的是「下拉框里必须有 mind」——那是旧 schema 把 Mind 塞进
    九选一里、于是「选中它」等于「把别的挤掉」的产物。现在 Mind 有自己的开关，
    两条一起钉：下拉里没有 mind、而 Mind 组第一行就是那个开关。免得下次又塞回去。

    2026-11-25（`WP-EXOCORTEX-ENTRY`）：那个下拉的清单**搬走后端了**——桌面那份
    手抄三项是**死代码**（后端真有 8+ 家，屏上一个都选不到，而 `memory.provider`
    当年被后端 schema 跳过 ⇒ 屏上连这一行都没有）⇒ 这一条改成钉「桌面不再留
    那份手抄清单」，而下拉里没有 mind 这件事由**后端那一份**说了算
    （`tests/plobi_cli/test_config_schema_options.py`）。
    """
    text = io.open(DESKTOP_CONSTANTS, encoding="utf-8").read()
    assert "'memory.provider':" not in text, "桌面又留了一份手抄的外脑清单"

    at = text.index("titleKey: 'settings.groups.memory.mind'")
    assert "memory.mind_enabled" in text[at:at + 400], "Mind 组里没有那个常驻开关"


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
    # `WP-MEMORY-LAYERS` 之后那一节的 `keys` 由 `MEMORY_GROUPS` 拼出来（两处各写一遍
    # 必然漂），所以真实的键清单在 `const MEMORY_GROUPS` 那一段里，不在这里。
    at = text.index("const MEMORY_GROUPS")
    block = text[at:text.index("export const SECTIONS")]
    exposed = re.findall(r"'(memory\.[a-z_.]+)'", block)
    assert len(exposed) >= 8, f"那一格只剩 {exposed}，整条分层没面可设"

    honoured = {"memory." + v for v in _MEMORY_LIMIT_KEYS.values()} | {
        "memory.memory_enabled", "memory.user_profile_enabled",
        "memory.write_approval", "memory.mind_write_approval",
        "memory.mind_write_auto_approve", "memory.mind_enabled",
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


def test_official_brain_does_not_take_the_third_party_slot(tmp_path, monkeypatch):
    """官方脑与至多一家第三方**可以同时在册**（`WP-MIND-COEXIST`）。

    前身 `test_official_provider_is_still_one_of_one` 钉的是反过来的事：mind 之后
    再来一家就被判掉——那正是「选了 honcho 就等于把 Mind 关掉」的代码形状。
    现在闸挪到第三方那一侧：第二家**第三方**才被判掉，Mind 不占那个槽。
    """
    manager = MemoryManager()
    manager.add_provider(_Stub(OFFICIAL_MEMORY_PROVIDER))
    manager.add_provider(_Stub("honcho"))
    assert [p.name for p in manager.providers] == [OFFICIAL_MEMORY_PROVIDER, "honcho"]

    manager.add_provider(_Stub("mem0"))
    assert [p.name for p in manager.providers] == [OFFICIAL_MEMORY_PROVIDER, "honcho"]


def test_the_same_provider_is_not_registered_twice(tmp_path, monkeypatch):
    """两路装载逻辑撞上同一家时不许叠影——叠了的话 sync_all 会把同一份记忆写两遍。"""
    manager = MemoryManager()
    manager.add_provider(_Stub(OFFICIAL_MEMORY_PROVIDER))
    manager.add_provider(_Stub(OFFICIAL_MEMORY_PROVIDER))
    assert [p.name for p in manager.providers] == [OFFICIAL_MEMORY_PROVIDER]


def test_sync_all_reaches_both_providers(tmp_path, monkeypatch):
    """「两边都能写」不是一句口号：一次 `sync_all` 必须走到两家。"""
    calls = []

    class _Counter(_Stub):
        def initialize(self, session_id=None, **kwargs):
            return None

        def sync_turn(self, user_content, assistant_content, **kwargs):
            calls.append(self.name)

    manager = MemoryManager()
    manager.add_provider(_Counter(OFFICIAL_MEMORY_PROVIDER))
    manager.add_provider(_Counter("honcho"))
    manager.sync_all("问", "答", session_id="s1")
    assert manager.flush_pending(timeout=5.0) is True
    assert sorted(calls) == ["honcho", OFFICIAL_MEMORY_PROVIDER], calls


def test_memory_enabled_flag_is_not_conflated_with_the_brain():
    """`memory_enabled` 管内置 MEMORY.md（§10.2 的内存层），不管外置脑。

    抬 provider 时顺手把这个开关也翻掉，就是让一个键同时拥有两层——
    他配置文件里现在是 false，这一笔没动它，这条断言防止下一笔动歪。
    """
    cfg = {"memory_enabled": False}
    assert cfg.get("memory_enabled", False) is False
    assert resolve_mind_enabled(cfg.get("mind_enabled")) is True

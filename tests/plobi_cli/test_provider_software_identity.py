"""provider 那一栏装的是**软件名**，不是网关的机器标识（WP-PROVIDER-SOFTWARE-IDENTITY）。

主人 11-17 18:5x：「两个都错了。我都说了 bezel 是内部名称。肯定是代码又写错了。
provider 下面适配的是一个一个软件名，而不是 bezel。」

钉六条不变量：

* 配置里写**软件名**（`workbuddy`）⇒ 解析得到**网关那条腿**，不是「不认识的 provider」；
* 运行时那条路（`resolve_runtime_provider`）也认软件名 ⇒ 聊天真能跑；
* 存量配置里还是**网关的机器标识**（`aigw`／`bezel`）⇒ **一次性迁成软件名并写回磁盘**；
* 迁完**写回新值** ⇒ 兼容表不留着；
* 模型 id 的前缀就是那家软件 ⇒ 软件名不用另造（数据里本来就有）；
* **软件名不进全局 provider 宇宙**（规范表／别名表）——那是「可配置的 provider」的
  宇宙，网关那几行不是一家一家可单独配置的 provider（见 `plobi_cli/models.py` 里
  `HUB_TRANSPORT` 那段注释）。
"""

import io
import os

import pytest

from plobi_cli.auth import hub_software_for_model, migrate_legacy_hub_provider, resolve_provider
from plobi_cli.models import (
    CANONICAL_PROVIDERS,
    HUB_SOFTWARE,
    HUB_SOFTWARE_IDS,
    HUB_TRANSPORT,
    config_provider_name,
    transport_for_software,
)


def _write_config(home, provider, default="workbuddy/hy4free"):
    """在一个假家里写一份配置（只碰这个假家，不碰真机器那一份）。"""
    os.makedirs(home, exist_ok=True)
    path = os.path.join(home, "config.yaml")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "model:\n"
            f"  provider: {provider}\n"
            f"  default: {default}\n"
            "toolsets:\n"
            "  - web\n"
        )
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把家指到一个临时目录（`PLOBI_HOME` 是仓里认的那个家）。"""
    import plobi_constants

    fake = str(tmp_path / "home")
    os.makedirs(fake, exist_ok=True)
    monkeypatch.setattr(plobi_constants, "PLOBI_HOME", fake, raising=False)
    monkeypatch.setenv("PLOBI_HOME", fake)
    return fake


def test_软件名解析得到网关那条腿(home):
    """配置里写软件名 ⇒ 解析得到网关（不是「不认识的 provider」）。"""
    _write_config(home, "workbuddy")
    assert resolve_provider("auto") == "bezel"


def test_运行时那条路也认软件名(home):
    """聊天真跑的那条路（`resolve_runtime_provider`）也得认软件名。

    它读配置里的 provider 直接往下走 ⇒ 配置里是软件名时若不换到传输，
    网关那条腿就认不出来（主人那台机器上表现为「换了模型就用不了了」）。
    """
    from plobi_cli.runtime_provider import resolve_requested_provider

    _write_config(home, "workbuddy")
    assert resolve_requested_provider() == "bezel"
    assert resolve_requested_provider("workbuddy") == "bezel"
    # 别家 provider 原样（不误伤）。
    _write_config(home, "openrouter")
    assert resolve_requested_provider() == "openrouter"


def test_模型_id的前缀就是那家软件():
    assert hub_software_for_model("workbuddy/hy4free") == "workbuddy"
    assert hub_software_for_model("qoder/sonus") == "qoder"
    # 不是软件的前缀 ⇒ 认不出来（那是别家的模型 id，比如 `anthropic/claude-…`）
    assert hub_software_for_model("anthropic/claude-sonnet-4.5") is None


def test_存量配置里的机器标识被一次性迁成软件名(home):
    """`provider: aigw`（网关改名前的机器标识）⇒ 迁成 `workbuddy` 并**写回磁盘**。"""
    path = _write_config(home, "aigw")
    assert resolve_provider("auto") == "bezel"

    with io.open(path, encoding="utf-8") as fh:
        written = fh.read()
    assert "provider: workbuddy" in written
    assert "aigw" not in written


def test_迁移只干一次_不是永久兼容表(home):
    """迁完写回新值 ⇒ 再读一次没有要迁的东西（不留兼容表）。"""
    _write_config(home, "bezel")
    resolve_provider("auto")

    path = os.path.join(home, "config.yaml")
    with io.open(path, encoding="utf-8") as fh:
        assert "provider: workbuddy" in fh.read()

    # 第二遍：配置已经是软件名 ⇒ 迁移函数不动它。
    import plobi_cli.config as cfg_mod

    with io.open(path, encoding="utf-8") as fh:
        raw = cfg_mod.read_raw_config()
    assert migrate_legacy_hub_provider(raw) is None


def test_配置里那个名字是软件名_机器标识只在映射里():
    """配置里写软件名；机器标识只留在传输映射里。"""
    assert config_provider_name("bezel", "workbuddy/hy4free") == "workbuddy"
    assert config_provider_name("bezel", "qoder/sonus") == "qoder"
    # 别家 provider 写自己的 slug。
    assert config_provider_name("openrouter", "anthropic/claude-sonnet-4.5") == "openrouter"
    # 传输映射：软件名 ⇒ 网关；别家原样。
    assert transport_for_software("workbuddy") == "bezel"
    assert transport_for_software("openrouter") == "openrouter"


def test_软件名不进全局provider宇宙():
    """**两条既有契约的守护**（这一刀差点把它们破了）：

    ① 网关那张 profile **不许声明别名**（别名会漏进全局别名表）——
       `tests/plobi_cli/test_bezel_provider_plugin.py::test_bezel_declares_no_aliases`；
    ② 规范表里有的 provider 必须在桌面 Providers 页**可配置**（GUI⇄CLI 的 parity）——
       `tests/plobi_cli/test_provider_parity.py`。软件名一旦进了那张表，就会被当成
       「另一个 provider」⇒ 抢走 env 归属、破掉 parity。
    """
    from providers import get_provider_profile

    assert get_provider_profile("bezel").aliases == ()

    slugs = {p.slug for p in CANONICAL_PROVIDERS}
    for software in HUB_SOFTWARE:
        assert software not in slugs, f"{software} 进了规范表 ⇒ 它被当成另一个 provider"
        assert software not in HUB_TRANSPORT or HUB_TRANSPORT[software] == "bezel"


def test_写入口原样落软件名():
    """`/api/model/set` 那条写入口：软件名**原样落盘**，不回落成 openrouter。"""
    from plobi_cli.web_server import _normalize_main_model_assignment

    provider, model = _normalize_main_model_assignment("workbuddy", "workbuddy/hy4free")
    assert provider == "workbuddy"
    assert model == "workbuddy/hy4free"

    # 别家照旧（不在软件名那张表里的行为一个字不动）。
    provider, _ = _normalize_main_model_assignment("openrouter", "anthropic/claude-sonnet-4.5")
    assert provider == "openrouter"


def test_软件名那张表和传输映射对得上():
    assert set(HUB_TRANSPORT) == set(HUB_SOFTWARE_IDS)
    assert set(HUB_TRANSPORT.values()) == {"bezel"}

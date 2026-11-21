"""provider 那一栏装的是**软件名**，不是网关的机器标识（WP-PROVIDER-SOFTWARE-IDENTITY）。

主人 11-17 18:5x：「两个都错了。我都说了 bezel 是内部名称。肯定是代码又写错了。
provider 下面适配的是一个一个软件名，而不是 bezel。」

钉六条不变量：

* 配置里写**软件名**（`workbuddy`）⇒ 解析得到**网关那条腿**，不是「不认识的 provider」；
* 运行时那条路（`resolve_runtime_provider`）也认软件名 ⇒ 聊天真能跑；
* 存量配置里还是**网关的现役传输名**（`bezel`）⇒ **一次性迁成软件名并写回磁盘**；
  更早那两代机器标识 11-19 已彻底剔除：碰上就如实报「不认识的 provider」，不迁不猜；
* 迁完**写回新值** ⇒ 兼容表不留着；
* 模型 id 的前缀就是那家软件 ⇒ 软件名不用另造（数据里本来就有）；
* **软件名不进全局 provider 宇宙**（规范表／别名表）——那是「可配置的 provider」的
  宇宙，网关那几行不是一家一家可单独配置的 provider（见 `plobi_cli/models.py` 里
  `HUB_TRANSPORT` 那段注释）。
"""

import io
import os

import pytest

from plobi_cli.auth import migrate_legacy_hub_provider, resolve_provider
from plobi_cli.models import (
    CANONICAL_PROVIDERS,
    HUB_SOFTWARE,
    HUB_SOFTWARE_IDS,
    config_provider_name,
)
from providers.hub_software import hub_group


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


def test_软件名解析成它自己(home):
    """配置里写软件名 ⇒ 解析得到**这家软件自己**（它已经是 provider）。

    11-18 第三处把 `bezel` 从 provider 宇宙里摘掉之后，软件名不再是「换到传输」
    的入口，而是直接成为 provider；传输映射只留给凭据与请求字段兜底。
    """
    _write_config(home, "workbuddy")
    assert resolve_provider("auto") == "workbuddy"
    assert hub_group().transport_for("workbuddy") == hub_group().group_id


def test_运行时那条路也认软件名(home):
    """聊天真跑的那条路（`resolve_runtime_provider`）也得认软件名。

    它读配置里的 provider 直接往下走 ⇒ 配置里是软件名时若不换到传输，
    网关那条腿就认不出来（主人那台机器上表现为「换了模型就用不了了」）。
    """
    from plobi_cli.runtime_provider import resolve_requested_provider

    _write_config(home, "workbuddy")
    assert resolve_requested_provider() == "workbuddy"
    assert resolve_requested_provider("workbuddy") == "workbuddy"
    # 别家 provider 原样（不误伤）。
    _write_config(home, "openrouter")
    assert resolve_requested_provider() == "openrouter"


def test_模型_id的前缀就是那家软件():
    assert hub_group().member_for_model("workbuddy/hy4free") == "workbuddy"
    assert hub_group().member_for_model("qoder/sonus") == "qoder"
    # 不是软件的前缀 ⇒ 认不出来（那是别家的模型 id，比如 `anthropic/claude-…`）
    assert hub_group().member_for_model("anthropic/claude-sonnet-4.5") is None


def test_存量配置里的机器标识被一次性迁成软件名(home):
    """`provider: bezel`（网关的**现役传输名**）⇒ 迁成 `workbuddy` 并**写回磁盘**。"""
    path = _write_config(home, "bezel")
    assert resolve_provider("auto") == "workbuddy"

    with io.open(path, encoding="utf-8") as fh:
        written = fh.read()
    assert "provider: workbuddy" in written
    assert "provider: bezel" not in written


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
    assert hub_group().transport_for("workbuddy") == hub_group().group_id
    assert hub_group().transport_for("openrouter") == "openrouter"


def test_软件名是provider宇宙的成员_bezel不是():
    """**两条既有契约的守护**：

    ① 网关那张 profile **不许声明别名**（别名会漏进全局别名表）——
       `tests/plobi_cli/test_bezel_provider_plugin.py::test_bezel_declares_no_aliases`；
    ② 规范表里有的 provider 必须在桌面 Providers 页**可配置**（GUI⇄CLI 的 parity）——
       `tests/plobi_cli/test_provider_parity.py`。软件名一旦进了那张表，就会被当成
       「另一个 provider」⇒ 抢走 env 归属、破掉 parity。
    """
    from providers import get_provider_profile

    assert get_provider_profile("bezel").aliases == ()

    slugs = {p.slug for p in CANONICAL_PROVIDERS}
    # 11-18 第三处反转：**软件名**才是 provider 宇宙的成员，`bezel` 不在里面。
    assert "bezel" not in slugs
    for software in HUB_SOFTWARE:
        assert software in slugs, f"{software} 没进规范表 ⇒ picker 里选不到这家软件"
        assert hub_group().owns_member(software), f"{software} 不在组节点成员里"


def test_写入口原样落软件名():
    """`/api/model/set` 那条写入口：软件名**原样落盘**，不回落成 openrouter。"""
    from plobi_cli.web_server import _normalize_main_model_assignment

    provider, model = _normalize_main_model_assignment("workbuddy", "workbuddy/hy4free")
    assert provider == "workbuddy"
    assert model == "workbuddy/hy4free"

    # 别家照旧（不在软件名那张表里的行为一个字不动）。
    provider, _ = _normalize_main_model_assignment("openrouter", "anthropic/claude-sonnet-4.5")
    assert provider == "openrouter"


def test_软件名那张表和组的成员对得上():
    """名册、注册表成员、组节点成员是同一份：三处不一致就是两套语义。"""
    assert set(HUB_SOFTWARE) == set(HUB_SOFTWARE_IDS) == set(hub_group().members)
    # 成员都走组那条腿（组级凭据挂在 owner 上）。
    for software in HUB_SOFTWARE_IDS:
        assert hub_group().transport_for(software) == hub_group().group_id


# ─── 第一处：迁移必须做在「显式 provider 抛错」之前 ────────────────────────


def test_显式传机器标识也走迁移(home):
    """`resolve_provider("bezel")` —— 不是 `auto`，是**显式**那个名字。

    这是第一处的病根：迁移原先只写在 `auto` 段里，而显式 provider 早在那之前
    就 `raise AuthError("Unknown provider")` 抛出去了，于是存量配置
    `provider: bezel` 永远走不到迁移 —— 换模型只剩一句红字。
    """
    path = _write_config(home, "bezel")

    assert resolve_provider("bezel") == "workbuddy"

    with io.open(path, encoding="utf-8") as fh:
        written = fh.read()
    assert "provider: workbuddy" in written
    assert "provider: bezel" not in written


def test_现役传输名能迁(home):
    """`bezel` 是唯一的现役传输名，还认得、还迁得动。"""
    _write_config(home, "bezel", default="qoder/sonus")
    assert resolve_provider("bezel") == "qoder"


@pytest.mark.parametrize("retired", ["aigw", "quota_hub"])
def test_已退役的机器标识不再迁_如实报不认识的provider(home, retired):
    """11-19「彻底剔除、不做兼容」：这两代连同读侧兼容壳一起删了。

    配置里还写着它们 ⇒ 走到「不认识的 provider」，且**不写盘**（不留任何
    猜测出来的迁移结果）。
    """
    from plobi_cli.auth import AuthError

    path = _write_config(home, retired, default="qoder/sonus")
    with io.open(path, encoding="utf-8") as fh:
        before = fh.read()

    with pytest.raises(AuthError):
        resolve_provider(retired)

    with io.open(path, encoding="utf-8") as fh:
        assert fh.read() == before


def test_迁移做在别名表之前(home):
    """`grok` 既是那家软件、也是 xAI 的近义词 —— 这里的答案是那家软件。

    别名表若先跑一遍，`grok` 会被改写成 `xai`，切换就落到别家去了
    （`providers.normalize_provider` 里同一个裁定）。
    """
    _write_config(home, "grok")
    assert resolve_provider("auto") == "grok"
    assert resolve_provider("grok") == "grok"
    # 厂商别名照旧：`anthropic` 还是 Anthropic，没被软件名那套吞掉。
    assert resolve_provider("anthropic") == "anthropic"

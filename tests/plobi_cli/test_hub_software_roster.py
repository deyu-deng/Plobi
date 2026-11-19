"""软件名册（`HUB_SOFTWARE`）只有一个真源；现役传输名**在任何入口**都迁得动，
退役的两代机器标识则一律如实报「不认识」。

11-18 那两处病根，各钉一条：

* **名册是手抄的** —— 手抄的 9 项里 6 项名字与网关真源对不上
  （``antigravity`` vs ``agy-cli``、``qoder_intl`` vs ``qoder-cn``、
  ``opencode_api`` vs ``opencode-zen-free``…），还漏了 13 家；本机自己就在跑
  ``opencode-zen-free/mimo-v2.6-flash-free``，前缀不在名单里 ⇒ 迁移只打一句 warning
  就放弃。现在这张表由 ``scripts/gen_hub_software.py`` 从
  ``bezel/bezel/providers/apps/magpie/providers.yaml`` 生成，本文件负责「漂了就红」。
* **迁移是死代码** —— 它写在 ``resolve_provider`` 的 ``auto`` 段里，而显式 provider
  早在上一屏就 ``raise AuthError("Unknown provider")`` 了，所以现役传输名
  ``provider: bezel`` 永远走不到迁移。现在前置到显式分支之前。
* **11-19 主人拍板彻底剔除旧标识** —— ``aigw``／``quota_hub`` 既不是现役传输名，
  也没有任何迁移路径：显式传它们就是「不认识的 provider」，不写盘、不改配置。
"""
import io
import os

import pytest

from plobi_cli.auth import hub_software_for_model, resolve_provider
from plobi_cli.models import HUB_SOFTWARE, HUB_SOFTWARE_IDS, transport_for_software

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROSTER = os.path.join(REPO_ROOT, "bezel", "bezel", "providers", "apps", "magpie", "providers.yaml")


def _write_config(home, provider, default="workbuddy/hy4free"):
    os.makedirs(home, exist_ok=True)
    path = os.path.join(home, "config.yaml")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "model:\n"
            f"  provider: {provider}\n"
            f"  default: {default}\n"
        )
    return path


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def home(tmp_path, monkeypatch):
    import plobi_constants

    fake = str(tmp_path / "home")
    os.makedirs(fake, exist_ok=True)
    monkeypatch.setattr(plobi_constants, "PLOBI_HOME", fake, raising=False)
    monkeypatch.setenv("PLOBI_HOME", fake)
    return fake


requires_roster = pytest.mark.skipif(
    not os.path.exists(ROSTER), reason="网关名册不在这个检出里（安装版/子模块缺失）"
)


@requires_roster
def test_名册与网关真源逐条一致():
    """表里那 22 行必须与 `providers.yaml` 一模一样，顺序也照抄。"""
    from scripts.gen_hub_software import load_roster

    expected = dict(load_roster())
    assert HUB_SOFTWARE == expected


@requires_roster
def test_生成块没漂():
    """`scripts/gen_hub_software.py --check` 必须过——不然就是有人手改了那张表。"""
    from scripts.gen_hub_software import main

    assert main(["--check"]) == 0


def test_本机那条腿的前缀认得出():
    """本机 dev 家就是这一行：`opencode-zen-free/mimo-v2.6-flash-free`。"""
    assert hub_software_for_model("opencode-zen-free/mimo-v2.6-flash-free") == "opencode-zen-free"
    assert transport_for_software("opencode-zen-free") == "bezel"


def test_名册不是九家小圈子():
    """手抄时代只有 9 家且半数名字是错的；真源是 22 家。"""
    assert len(HUB_SOFTWARE_IDS) >= 22
    for software in ("agy-cli", "opencode-zen-free", "qoder-cn", "workbuddy-ai", "kiro", "cline"):
        assert software in HUB_SOFTWARE_IDS


def test_显式provider传机器标识也能迁_不再抛Unknown(home):
    """旧代码这一段直接 `Unknown provider`——迁移在下一屏，永远够不到。

    11-19 起只有**现役传输名**（`bezel`）还走迁移；更早那两代已彻底剔除。
    """
    path = _write_config(home, "bezel")
    assert resolve_provider("bezel") == "workbuddy"
    written = _read(path)
    assert "provider: workbuddy" in written
    assert "provider: bezel" not in written


def test_显式provider传opencode_zen_free也能迁(home):
    """本机 dev 家的真实形状：provider 还是传输名，default 是免费池那条腿。"""
    path = _write_config(home, "bezel", default="opencode-zen-free/mimo-v2.6-flash-free")
    assert resolve_provider("bezel") == "opencode-zen-free"
    written = _read(path)
    assert "provider: opencode-zen-free" in written
    assert "provider: bezel" not in written


def test_运行时显式传机器标识也迁(home):
    from plobi_cli.runtime_provider import resolve_requested_provider

    path = _write_config(home, "bezel")
    assert resolve_requested_provider("bezel") == "workbuddy"
    assert "provider: workbuddy" in _read(path)


@pytest.mark.parametrize("retired", ["aigw", "quota_hub"])
def test_已退役的机器标识不再迁_如实报不认识(home, retired):
    """11-19「彻底剔除、不做兼容」：配置里还写这两个名字 ⇒ 不迁、不猜、不改盘。"""
    from plobi_cli.auth import AuthError

    path = _write_config(home, retired)
    before = _read(path)

    with pytest.raises(AuthError):
        resolve_provider(retired)

    assert _read(path) == before


def test_软件名自己就是provider(home):
    """11-18 第三处之后：软件名不再换成 `bezel`，它本身就是 provider。"""
    _write_config(home, "qoder")
    assert resolve_provider("qoder") == "qoder"
    assert transport_for_software("qoder") == "bezel"


def test_别家provider不被名册抢走(home):
    """名册只收网关那几行；OpenRouter 这类照旧走自己的路。"""
    assert resolve_provider("openrouter") == "openrouter"
    assert transport_for_software("openrouter") == "openrouter"

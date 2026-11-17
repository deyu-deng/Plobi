"""旧机器标识仍要解析得到网关（2026-11-17）。

主人 11-17 18:5x 截图那条红条「Unknown provider 'aigw'」的根因：**这一台**
``config.yaml`` 的 ``model.provider`` 是从 11-15 改名前写下来的 ``aigw``；
11-15 那刀按「内测版没有用户，不用管老配置文件」把读侧兼容表删了 ——
可内测版的用户**就是这一台** ⇒ 删完换模型就只剩那一句红条。

这一条判据钉的是**机器侧**：旧机器标识仍解析得到网关。

**屏上不许出现网关名**：那一栏列的是一家一家软件（WorkBuddy／Antigravity…，
见 ``apps/desktop/src/app/settings/provider-rows.ts`` 与
``plugins/model-providers/bezel`` 的 ``display_name``）。这里一个字都不钉文案。
"""

from plobi_cli.auth import PROVIDER_REGISTRY, resolve_provider


def test_旧机器标识_aigw_仍解析得到网关():
    assert "bezel" in PROVIDER_REGISTRY, (
        "网关那张 provider profile 没注册 —— 别名表补了也解析不出来（先修 profile）"
    )
    assert resolve_provider("aigw") == "bezel"


def test_旧机器标识大小写与前后空白都认():
    assert resolve_provider("AIGW") == "bezel"
    assert resolve_provider("  aigw  ") == "bezel"


def test_认旧值不改变新值():
    """补旧值不许把别的一起改了：网关自己的名字照旧解析得到自己。"""
    assert resolve_provider("bezel") == "bezel"

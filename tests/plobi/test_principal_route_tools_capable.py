"""The leg an L2 分身 actually gets must be able to carry tools (`WP-L2-ROUTE-TOOLS`, 裁定 145 的地基).

10-16 现核（这台，两条能复跑的读数）：`bezel/bezel/registry.py` 把 `workbuddy` 绑到
`WorkbuddyApiProvider`（`providers/workbuddy.py:71-78` 写 `tools=True`），真发一次带 `tools` 的请求
2.0 秒回 `finish_reason=tool_calls` —— 本格立案时那句「子进程走的是 `tools=False` 那条腿」**前提已翻**。
但当初让它成立的那个风险还在，而且只需要一次提交就能回来：`bezel/config.yaml` 与 `bezel/bezel/registry.py`
是分开的两档，谁把 `workbuddy` 这个键重新绑回 hybrid/CLI（`tools=False`），目录、面板、`plobi_agent_ask`
都不会报错，只会让「必派清单」变成"L1 派出去、L2 干不了活、180 秒后什么也没回来"。所以这条不靠肉眼，
靠一个会红的测试。

不联网：读的是仓里的映射与那份随包的 provider 模型清单。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bezel"))

from bezel.registry import PROVIDER_CLASSES  # noqa: E402

#: 这条腿的前缀就写在这里，不再从 ``plobi.agents.registry`` 借：那个常量当初只服务
#: 早报那条路（``pick_workbuddy_model``），随裁定 183 一起退役了。本档钉的是
#: **网关那份 config.yaml** 的形状，值属于这份契约。
WORKBUDDY_PREFIX = "workbuddy/"
_KEY = WORKBUDDY_PREFIX.strip("/")

#: 11-2x 起网关只剩**一条**作业腿：`magpie`。那 22 家软件（含 `workbuddy`）是它转发的
#: 目标，不再是并联的 provider 键 —— 所以「分身前缀那条腿能不能带工具」这句话，今天
#: 问的是 `magpie`。键名与软件名从此不同源，别再拿 `workbuddy` 去 `PROVIDER_CLASSES` 里查。
_CARRYING_KEY = "magpie"


def _hub_models_for_key(key: str) -> list[str]:
    config = yaml.safe_load((REPO / "bezel" / "config.yaml").read_text(encoding="utf-8"))
    block = (config.get("providers") or {}).get(key) or {}
    return [str(m) for m in (block.get("models") or [])]


def test_the_provider_bound_to_the_fenshen_prefix_can_carry_tools():
    """转发 `workbuddy/*` 的那些模型的那条腿，必须声明 `tools=True`，否则 L2 拿到派工也干不了活。"""
    provider = PROVIDER_CLASSES.get(_CARRYING_KEY)

    assert provider is not None, (
        f"bezel 不再认识 provider 键 {_CARRYING_KEY!r}——这一格钉的就是「谁在转发那些软件」"
    )
    caps = getattr(provider, "default_capabilities", None)

    assert caps is not None and caps.tools is True, (
        f"{_CARRYING_KEY} 绑到了 {provider.__name__}，它不带 tools。裁定 145 那张「必派清单」于是跑不通：L1 把作业派给"
        f" L2，L2 那条腿不能用工具，只能回一段文字，然后在 `plobi_agent_ask` 的时间界里空转。"
        f"要么把键绑回带 tools 的那条腿，要么在本格（`WP-L2-ROUTE-TOOLS`）里把它重新立案——别默认它还在。"
    )


def test_the_hubs_model_list_for_that_key_stays_under_one_prefix():
    """网关那份清单里若是静态列了模型，每条都得带 `provider/` 前缀。

    11-11：`pick_workbuddy_model()` 随裁定 183 退役之后，这份清单可以整段不写——
    `config.yaml` 那段现在就是这么写的（名单由 `refresh_models` 向上游现读，见文件头
    第 9-10 行）。所以"空清单"不算红；这里只守形状：**一旦静态列了模型，就不能是裸名**
    ——裸名在 `splitQualified` 那只手上没有归属，UI 与面板都会认错家。

    11-2x：`providers:` 段今天只剩 `magpie` 一段且不列模型，所以这条现在是形状兜底，
    不是对某一家软件的断言。前缀也**不再假定是 `workbuddy/`**：转发器名下的模型用的是
    软件名（`workbuddy/…`、`antigravity/…`），要求 `magpie/` 前缀反而是错的。
    """
    models = _hub_models_for_key(_CARRYING_KEY)

    strays = [m for m in models if "/" not in m]

    assert not strays, f"这些模型挂在 {_CARRYING_KEY} 名下却是裸名，分不出归属：{strays}"

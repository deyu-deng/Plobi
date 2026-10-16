"""The leg an L2 分身 actually gets must be able to carry tools (`WP-L2-ROUTE-TOOLS`, 裁定 145 的地基).

10-16 现核（这台，两条能复跑的读数）：`aigw/aigw/registry.py` 把 `workbuddy` 绑到
`WorkbuddyApiProvider`（`providers/workbuddy.py:71-78` 写 `tools=True`），真发一次带 `tools` 的请求
2.0 秒回 `finish_reason=tool_calls` —— 本格立案时那句「子进程走的是 `tools=False` 那条腿」**前提已翻**。
但当初让它成立的那个风险还在，而且只需要一次提交就能回来：`aigw/config.yaml` 与 `aigw/aigw/registry.py`
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
sys.path.insert(0, str(REPO / "aigw"))

from aigw.registry import PROVIDER_CLASSES  # noqa: E402
from plobi.agents.registry import WORKBUDDY_PREFIX  # noqa: E402

_KEY = WORKBUDDY_PREFIX.strip("/")


def _hub_models_for_key(key: str) -> list[str]:
    config = yaml.safe_load((REPO / "aigw" / "config.yaml").read_text(encoding="utf-8"))
    block = (config.get("providers") or {}).get(key) or {}
    return [str(m) for m in (block.get("models") or [])]


def test_the_provider_bound_to_the_fenshen_prefix_can_carry_tools():
    """`workbuddy/*` 这条腿必须声明 `tools=True`，否则 L2 拿到派工也干不了活。"""
    provider = PROVIDER_CLASSES.get(_KEY)

    assert provider is not None, f"aigw 不再认识 provider 键 {_KEY!r}——分身模型的前缀要与网关的键同源"
    caps = getattr(provider, "default_capabilities", None)

    assert caps is not None and caps.tools is True, (
        f"{_KEY} 绑到了 {provider.__name__}，它不带 tools。裁定 145 那张「必派清单」于是跑不通：L1 把作业派给"
        f" L2，L2 那条腿不能用工具，只能回一段文字，然后在 `plobi_agent_ask` 的时间界里空转。"
        f"要么把键绑回带 tools 的那条腿，要么在本格（`WP-L2-ROUTE-TOOLS`）里把它重新立案——别默认它还在。"
    )


def test_the_hubs_model_list_for_that_key_stays_under_one_prefix():
    """网关那份清单里属于 `workbuddy` 的模型，必须都在 `WORKBUDDY_PREFIX` 之下。

    `pick_workbuddy_model()` 是按前缀挑的：混进别的前缀（例如退役过的 `workbuddy_api/*`）就会让
    分身拿到一条与 tools 判据不同源的腿，而这条测试正是那件事的警报。
    """
    models = _hub_models_for_key(_KEY)

    assert models, f"aigw/config.yaml 里 providers.{_KEY}.models 空了——那 L2 没有可选模型"
    strays = [m for m in models if not m.startswith(WORKBUDDY_PREFIX)]

    assert not strays, f"这些模型挂在 {_KEY} 名下却不在 {WORKBUDDY_PREFIX} 前缀里，前缀挑选会漏掉它们：{strays}"

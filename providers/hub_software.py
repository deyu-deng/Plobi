"""网关适配的**软件名**——它们才是 provider；`bezel` 只是传输。

主人的规矩（11-17 18:5x / 11-18）：「bezel 是内部名称。provider 下面适配的是一个
一个软件名，而不是 bezel。」所以：

* 每个软件（``workbuddy``、``opencode-zen-free``…）**自己就是一条 provider**：
  有自己的名字、自己的屏上标签、自己的模型前缀（``opencode-zen-free/mimo-...``）。
* ``bezel`` 不再是 provider —— 它只是这些腿共用的**传输**（loopback 上那个
  OpenAI 兼容端点）。它不进 ``PROVIDER_REGISTRY``、不上屏、不进 picker。
* 名册只有一份真源：``bezel/bezel/providers/apps/magpie/providers.yaml``。
  下面那张表由 ``scripts/gen_hub_software.py`` 生成，手抄一次错一次。

共用同一份凭据与端点这件事不矛盾：profile 之间共享 ``base_url`` / ``env_vars`` /
``fallback_api_key`` 的实现，但**名字**是 22 个软件，不是一个网关。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from providers import register_provider
from providers.base import ProviderProfile

from plobi.agents.registry import (
    BEZEL_KEY_ENV_VARS,
    BEZEL_URL_ENV_VARS,
    bezel_api_key,
    bezel_base_url,
)

#: 传输的名字。**只在内部用**：日志、doctor 行 id、传输映射的值。
#: 它不是 provider —— 别拿它去 `PROVIDER_REGISTRY` 里查。
TRANSPORT_NAME = "bezel"

_KEY_VARS = BEZEL_KEY_ENV_VARS
_URL_VARS = BEZEL_URL_ENV_VARS

_BASE_URL = bezel_base_url()

#: 模型 id 的前缀落在这一组里 ⇒ 这个 provider 名是**软件名**，不是机器名。
#:
#: id 就是网关名册里的 app id，也正是**模型 id 的前缀**（``workbuddy/hy4free`` ⇒
#: ``workbuddy``）—— 「哪一家软件在服务这个模型」这件事，数据里本来就有。
#: **这张表由 `scripts/gen_hub_software.py` 生成，不许手改。**
# >>> HUB_SOFTWARE (generated from bezel/bezel/providers/apps/magpie/providers.yaml) >>>
HUB_SOFTWARE: dict[str, str] = {
    "workbuddy":           'WorkBuddy',
    "workbuddy-ai":        'WorkBuddy AI',
    "opencode-zen-free":   'OpenCode Free',
    "qoder":               'Qoder',
    "qoder-cn":            'Qoder CN',
    "qwenwork":            'QwenWork',
    "qwenwork-enterprise": 'QwenWork 企业版',
    "kiro":                'Kiro',
    "cline":               'Cline',
    "github-copilot":      'GitHub Copilot',
    "cursor":              'Cursor',
    "agy-cli":             'Antigravity CLI',
    "devin":               'Devin',
    "grok":                'Grok',
    "trae-cn":             'Trae CN',
    "trae-global":         'Trae Global',
    "mimo-app":            'Xiaomi MiMo',
    "minimax-code":        'MiniMax Code',
    "minimax-code-global": 'MiniMax Code (Global)',
    "joycode":             'JoyCode',
    "tabbit":              'Tabbit',
    "zcode":               'ZCode',
}
# <<< HUB_SOFTWARE <<<

HUB_SOFTWARE_IDS = frozenset(HUB_SOFTWARE)


def _host_and_port(base_url: str) -> str:
    """``host:port``；没有 netloc 时返回 ""。

    声明成 ``hostname``（而不是让 ``ProviderProfile`` 自己取裸 host）是为了让
    ``agent/model_metadata.py`` 的 URL→provider 反查保持**端口精确**：裸
    ``127.0.0.1`` 会被当成任何本地端点的子串，用户自己的 Ollama（``127.0.0.1:11434``）
    就会被反查成网关。
    """
    return urlparse(base_url).netloc.lower()


#: Plobi 自己用的思考档位（``plobi_constants.VALID_REASONING_EFFORTS``）。
_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")


class HubTransportProfile(ProviderProfile):
    """网关那条腿的共用行为：凭据兜底 + 请求里那几个自家字段。

    这一坨**不注册**（见 ``TRANSPORT_NAME``）：它是 22 个软件 profile 的共同父类，
    也是 ``bezel`` 这个名字在内部路径上还能被认出来的地方。
    """

    def fallback_api_key(self) -> str:
        """用户没给显式凭据时网关自己的答案。

        ``plobi_cli.auth.resolve_api_key_provider_credentials`` 最后才走到这里，
        所以聊天与探模型的路径从同一个入口解析。值来自 agents 层，
        顺序与标签在 ``plobi.agents.registry.bezel_api_key`` / ``bezel_credential_source``。
        """
        return bezel_api_key()

    def build_api_kwargs_extras(self, *, reasoning_config=None, context_length=None, **context):
        """思考档位走 OpenAI 自己的字段，上下文窗口放 ``extra_body``。

        ``context_length`` **必须**进 ``extra_body``：``api_kwargs`` 是
        ``chat.completions.create()`` 的 Python 实参，SDK 有 ``reasoning_effort``
        但没有 ``context_length`` —— 放到顶层会在发请求前就
        ``TypeError: unexpected keyword argument 'context_length'``。
        """
        extra: dict[str, Any] = {}
        top: dict[str, Any] = {}
        if isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is not False:
            effort = str(reasoning_config.get("effort") or "").strip().lower()
            if effort in _EFFORTS:
                top["reasoning_effort"] = effort
        if isinstance(context_length, int) and not isinstance(context_length, bool) and context_length > 0:
            extra["context_length"] = context_length
        return extra, top


class HubSoftwareProfile(HubTransportProfile):
    """一家软件 = 一条 provider；端点与凭据是网关那一份。"""


def software_profile(software_id: str) -> HubSoftwareProfile:
    sid = (software_id or "").strip().lower()
    label = HUB_SOFTWARE.get(sid, sid)
    return HubSoftwareProfile(
        name=sid,
        aliases=(),  # 软件名不进全局别名表：它就是它自己
        api_mode="chat_completions",
        display_name=label,
        description=f"{label} — 订阅额度里的那一家（经本地网关转出去）",
        env_vars=_KEY_VARS + _URL_VARS,
        base_url=_BASE_URL,
        hostname=_host_and_port(_BASE_URL),
        auth_type="api_key",
        # 网关按模型自选后端；没有可兜底的策划清单。
        supports_vision=False,
    )


#: ``bezel`` 这个名字在内部路径上的替身：**不注册**，只在查不到 provider 时兜底。
#: 用它只是为了 `get_provider_profile("bezel")` 还能拿到凭据兜底与请求字段，
#: 而它**不在** ``list_providers()`` 里 ⇒ 不上屏、不进 picker、不进 parity 契约。
TRANSPORT_PROFILE = HubTransportProfile(
    name=TRANSPORT_NAME,
    aliases=(),
    api_mode="chat_completions",
    display_name="订阅额度",
    description="本地额度网关（内部传输名，不该上屏）",
    env_vars=_KEY_VARS + _URL_VARS,
    base_url=_BASE_URL,
    hostname=_host_and_port(_BASE_URL),
    auth_type="api_key",
    supports_vision=False,
)


def register_software_providers() -> None:
    """把 22 家软件注册成 provider（幂等）。"""
    for sid in HUB_SOFTWARE:
        register_provider(software_profile(sid))


register_software_providers()

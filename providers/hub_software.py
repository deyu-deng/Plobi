"""网关适配的**软件名**——它们才是 provider；`bezel` 是它们**组的 owner**。

主人的规矩（11-17 18:5x / 11-18）：「bezel 是内部名称。provider 下面适配的是一个
一个软件名，而不是 bezel。」11-2x 又往前一步（方案 C）：这层关系不是「一个 provider
加一堆兄弟」，而是**一个组节点**——

* ``HUB_GROUP`` 是一个 ``ProviderGroup``：``bezel`` 是它的 ``owner``（组级凭据、端点、
  请求字段都在这里），名册里那几家软件是它的 ``members``（各自可选、各自上屏一行）。
* 每个成员（``workbuddy``、``opencode-zen-free``…）**自己就是一条 provider**：
  自己的名字、自己的屏上标签、自己的模型前缀（``opencode-zen-free/mimo-...``）。
* ``bezel`` 是组 owner，**不是**并列的一条 provider：它不进 ``_REGISTRY``、
  不上屏、不进 picker，只在「查不到 provider」时作为组级兜底身份出现。
* 名册只有一份真源：``bezel/bezel/providers/apps/magpie/providers.yaml``。
  下面那张表由 ``scripts/gen_hub_software.py`` 生成，手抄一次错一次。

**外部不要再判 ``is_hub_software`` 了**：要问的是 ``hub_group().owns(name)``
（成员或组自己）或 ``hub_group().owns_member(name)``（只有那几家）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from providers import register_provider, register_provider_group
from providers.base import ProviderGroup, ProviderProfile

from plobi.agents.registry import (
    BEZEL_KEY_ENV_VARS,
    BEZEL_URL_ENV_VARS,
    bezel_api_key,
    bezel_base_url,
)

#: 组的 id，同时也是组 owner 的名字。**只在内部用**：日志、doctor 行 id、
#: 存量配置迁移。它不是一条并列的 provider —— 别拿它去 `PROVIDER_REGISTRY` 里查。
GROUP_ID = "bezel"

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
    "opencode-zen-free":   'OpenCode Zen Free',
    "qoder":               'Qoder',
    "qoder-cn":            'Qoder CN',
    "qwenwork":            'QwenWork',
    "qwenwork-enterprise": 'QwenWork Enterprise',
    "kiro":                'Kiro',
    "cline":               'Cline',
    "github-copilot":      'GitHub Copilot',
    "cursor":              'Cursor',
    "antigravity":         'Antigravity',
    "agy-cli":             'Antigravity CLI',
    "google":              'Google',
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


class HubGroupProfile(ProviderProfile):
    """组**级**行为的承载者：凭据兜底 + 请求里那几个自家字段。

    这一坨**不注册**（见 ``GROUP_ID``）：它是 这些软件 profile 的共同父类，
    也是组 owner（``GROUP_OWNER``）的类。「组级的凭据/端点/请求字段只有一份」
    这件事，落到代码上就是**只有这里实现一次**。
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


class HubSoftwareProfile(HubGroupProfile):
    """一家软件 = 一条 provider；端点与凭据是组那一份。"""


def software_profile(software_id: str) -> HubSoftwareProfile:
    sid = (software_id or "").strip().lower()
    label = HUB_SOFTWARE.get(sid, sid)
    return HubSoftwareProfile(
        name=sid,
        aliases=(),  # 软件名不进全局别名表：它就是它自己
        group=GROUP_ID,  # 归属写进 profile：散落判断不必再查名单
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


#: 组 owner：``bezel`` 这个名字在内部路径上的**唯一**承载者。**不注册**进
#: ``_REGISTRY`` —— 用它只是为了 `get_provider_profile("bezel")` 还能拿到组级凭据
#: 兜底与请求字段，而它**不在** ``list_providers()`` 里 ⇒ 不上屏、不进 picker、
#: 不进 parity 契约。
GROUP_OWNER = HubGroupProfile(
    name=GROUP_ID,
    aliases=(),
    group=GROUP_ID,
    api_mode="chat_completions",
    display_name="订阅额度",
    description="本地额度网关（组级 owner，不该单独上屏）",
    env_vars=_KEY_VARS + _URL_VARS,
    base_url=_BASE_URL,
    hostname=_host_and_port(_BASE_URL),
    auth_type="api_key",
    supports_vision=False,
)


#: 组节点：owner 承载组级行为，名册里那几家软件是成员。**组不是一行可选 provider**，
#: 所以它只进 ``_GROUPS``、不进 ``_REGISTRY``。
HUB_GROUP = ProviderGroup(
    group_id=GROUP_ID,
    display_name="订阅额度",
    description="一个本地网关 + 名册里那几家软件：组级凭据与端点归 owner，软件名各自可选。",
    owner=GROUP_OWNER,
    members=tuple(HUB_SOFTWARE),
    member_labels=dict(HUB_SOFTWARE),
    show_owner_in_picker=False,
)

register_provider_group(HUB_GROUP)


def hub_group() -> ProviderGroup:
    """订阅额度这组的**唯一入口**。

    以前每个调用点各自比名字（``is_hub_software`` / ``transport_for_software`` /
    ``software_name_for_model`` …），一处漏改就两套语义。现在只有一个问题：
    **这个名字归不归这组管** —— 成员还是组自己，都由组节点回答。
    """
    return HUB_GROUP


def register_software_providers() -> None:
    """把名册里那几家软件注册成 provider（幂等）。"""
    for sid in HUB_SOFTWARE:
        register_provider(software_profile(sid))


register_software_providers()

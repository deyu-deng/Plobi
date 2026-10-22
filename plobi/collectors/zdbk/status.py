"""`zdbk` 这一条的状态词表 —— 每一格是一句不同的话，不许合并。

形状照 `plobi/collectors/zju/status.py`（那一族是这一层的先例）：采不到东西必须说得出
为什么，**绝不 ``return []`` 冒充"这一轮确实没有"**。
"""

from __future__ import annotations

STATE_READY = "ready"
STATE_DISABLED = "disabled"
STATE_NO_CREDENTIAL = "no_credential"
STATE_AUTH_FAILED = "auth_failed"
STATE_CAPTCHA_REQUIRED = "captcha_required"
STATE_NETWORK_UNREACHABLE = "network_unreachable"
STATE_EMPTY_TERM = "empty_term"
STATE_THROTTLED = "throttled"

SOURCE_STATES: tuple[str, ...] = (
    STATE_READY, STATE_DISABLED, STATE_NO_CREDENTIAL, STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED, STATE_NETWORK_UNREACHABLE, STATE_EMPTY_TERM, STATE_THROTTLED,
)
FAILURE_STATES: tuple[str, ...] = tuple(s for s in SOURCE_STATES if s != STATE_READY)

_NOTES = {
    STATE_READY: "课表这一家回了东西（条数在每轮自己的读数里）。",
    STATE_DISABLED: "这一条被关了（`zdbk.json` 里 `enabled=false`）：不会去碰学校。",
    STATE_NO_CREDENTIAL: "根 `.env` 里没有那对账密 ⇒ 一次都不该打学校。",
    STATE_AUTH_FAILED: "教务端点没回认识的信封：会话掉了，或者学校改了页面。两种都要人去看，不许合成「没数据」。",
    STATE_CAPTCHA_REQUIRED: "这一轮要人工验证码 ⇒ 按用户 10-21 拍的口径**报一句并停手**，不自动识别、不重试。",
    STATE_NETWORK_UNREACHABLE: "打不到学校（这台可达性会随代理/TUN 翻面）：这不是凭据问题，两句话不同。",
    STATE_THROTTLED: "距上一轮真打不够久（节流）：这一轮连学校都不碰，不是「学校没数据」。",
    STATE_EMPTY_TERM: "会话成了但这一轮一条都没拿到：多半是学年/学期码（`xnm/xqm`)不对，不是「这学期没课」。",
}


def note_for(state: str) -> str:
    if state not in _NOTES:
        return f"没记到的教务状态 {state!r}：不去猜它属于哪一种。"
    return _NOTES[state]


class ZdbkUnavailable(RuntimeError):
    """带 ``state`` + 那一句为什么；`sweep` 原样收进读数，不转述、不总结。"""

    def __init__(self, state: str, detail: str = "") -> None:
        super().__init__(detail or note_for(state))
        self.state = state
        self.detail = detail or note_for(state)

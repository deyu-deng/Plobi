"""「学在浙大」采不到的那几句话 —— 一格一句，不许合并、不许静默返 0。

拍下来的形状是**三格失败话**（裁定 114 末段 / 裁定 115 二）：**认证失败**、**要人工验证码**、
**网络不可达**。这里在此之上多两格，两条都不是"再加一档分类学"，而是没有它就必然说谎：

- ``no_credential``：根 ``.env`` 里压根没有账密。把它并进「认证失败」会让人去改一个
  根本没配的密码（与判据 7 那条「入口不偷默认」同形：缺前置要说缺前置）。
- ``login_changed``：站方改了登录页/公钥接口，我们这套读法取不到表单。把它并进
  「认证失败」是最坏的一格——用户会去改密码，而该改的是我们的代码。
  ⇒ **这一格要不要收进三格，请治理线判**；我不肯做的是拿它冒充「用户名或密码不对」。

``throttled`` 不是失败，是"这轮没去碰学校"的读数（礼貌面上限，见 :mod:`.collector`）。
"""

from __future__ import annotations

STATE_READY = "ready"
STATE_DISABLED = "disabled"
STATE_NO_CREDENTIAL = "no_credential"
STATE_AUTH_FAILED = "auth_failed"
STATE_CAPTCHA_REQUIRED = "captcha_required"
STATE_NETWORK_UNREACHABLE = "network_unreachable"
STATE_LOGIN_CHANGED = "login_changed"
STATE_THROTTLED = "throttled"

SOURCE_STATES: tuple[str, ...] = (
    STATE_READY,
    STATE_DISABLED,
    STATE_NO_CREDENTIAL,
    STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED,
    STATE_NETWORK_UNREACHABLE,
    STATE_LOGIN_CHANGED,
    STATE_THROTTLED,
)

#: 这三格是拍下来的失败话（不许拆、不许并）。「学校拒了」那一格内部**不再细分**：
#: zjuam 对「账号不存在」与「密码错」给的是同一个回话（治理线拿不存在的账号证过），
#: 拆两格就是编出一个站方没给出的区别。
FAILURE_STATES: tuple[str, ...] = (
    STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED,
    STATE_NETWORK_UNREACHABLE,
)

_NOTES = {
    STATE_READY: "学校那边进去了，作业清单拿到了（这一句不等于「有作业」——真 0 条也是这一格）。",
    STATE_DISABLED: (
        "配置里 ``enabled=false``：这一路是关着的。没去碰学校，也没落库——"
        "这一格与「采到了 0 条」必须分开（判据 2 那颗雷：0 行不许看着像正常）。"
    ),
    STATE_NO_CREDENTIAL: (
        "没配学在浙大的账密（根 .env 的 ZJU_USERNAME / ZJU_PASSWORD）："
        "没去碰学校，也没落库。"
    ),
    STATE_AUTH_FAILED: (
        "学校拒了这次登录（用户名或密码不对）。站方不区分「账号不存在」和「密码错」，"
        "这句话没法再细分；先核对读到的凭据，别连着多试——失败计数是风控的入口。"
    ),
    STATE_CAPTCHA_REQUIRED: (
        "学校这次要人工验证码。那是站方服务端的开关，不是这个信源的性质；"
        "无人值守采不了，等你手工登一次再说。"
    ),
    STATE_NETWORK_UNREACHABLE: (
        "到 courses.zju.edu.cn / zjuam.zju.edu.cn 这条路没通。"
        "这台机器的可达性会随代理/TUN 开关翻面，这句报的是刚才那一瞬间，不是永久属性。"
    ),
    STATE_LOGIN_CHANGED: (
        "站方登录页的形状变了（取不到登录表单，公钥或业务端点也对不上）："
        "这不是你密码错，是我们这套读法要跟新——别去改密码。"
    ),
    STATE_THROTTLED: (
        "离上一轮还没到最短间隔（礼貌面上限），这一轮没去碰学校。"
    ),
}


def note_for(state: str) -> str:
    """一句人话。认不出的状态**不编句子**——那正是"面上永远正常"的成因。"""
    if state not in _NOTES:
        return f"没记到的学在浙大状态 {state!r}：不去猜它属于哪一种。"
    return _NOTES[state]


class ZjuUnavailable(RuntimeError):
    """采不到东西时抛的那一个，带 ``state`` + 那一句。

    存在的理由只有一条：**采不到必须说得出为什么**，不许 ``return []``。
    真 0 条作业走 :data:`STATE_READY`（进得去、确实没有），与本族每一格分得开。
    """

    def __init__(self, state: str, detail: str = ""):
        self.state = state
        self.note = note_for(state)
        self.detail = detail
        super().__init__(f"{self.note}（state={state}）" + (f"｜{detail}" if detail else ""))

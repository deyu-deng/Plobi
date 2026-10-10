"""学在浙大的登录链（CAS ``fm1`` + 站方 RSA 形状）—— 配方按 10-10 实测抄形状，不抄代码。

一次真跑通的顺序（治理线 19:3x 两次成功、`/api/todos` 回 200/48 条；本线按同一形状实现）：

1. ``GET https://courses.zju.edu.cn/login`` —— 未登录时被领到 zjuam 的 CAS 登录页（页面里有
   ``<form id="fm1" action=…>`` 与隐藏的 ``execution`` / ``uuid``）。
2. ``GET /cas/v2/getPubKey`` —— ``{modulus, exponent}``，都是 16 进制串（512 位模数、e=65537）。
3. ``GET /cas/v2/getKaptchaStatus`` —— 站方服务端要不要验证码。**开关在站方**，不是这个信源的性质；
   它给 ``true`` 就当场停手报「要人工验证码」，不再 POST（多打一次就是多送一次失败计数）。
4. 口令按站方 ``security.js`` 的算法加密：反转 → 按 ``chunkSize`` 补零 → 每块小端转整数 →
   ``pow(m, e, n)`` → 16 进制 → 多块以空格连接。``chunkSize = 2 * (十六进制位数/4 - 1)``，
   512 位模数就是 62 字节。
5. ``POST`` 回表单的 ``action``，字段 ``username / password / authcode / rememberMe / execution /
   _eventId=submit / ys / uuid``。
6. **成不成只认业务端点**：登录成功后 ``GET /api/user`` 仍回 403「您没有权限完成此操作」，那不是掉线
   （同会话 ``/api/todos`` 立刻 200/48）⇒ 判活只打 ``/api/todos``，认的是「跟着重定向走完之后拿回的是
   不是那份 JSON」。裁定 114 那句「只认业务端点 200/302」落到实现上就是这个意思：
   302 本身不证明进去了，跟着它走完、落在业务 JSON 上才算。

许可边界：celechron 是 GPL-3.0-only，**这里只借它验证过的接口形状与字段名，一行代码都没抄**
（`Docs/reference/celechron-survey.md` §4.5 与本包任务书的许可条）。
"""

from __future__ import annotations

import html as htmllib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol

from .status import (
    STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED,
    STATE_LOGIN_CHANGED,
    STATE_NETWORK_UNREACHABLE,
    STATE_READY,
)

COURSES_ORIGIN = "https://courses.zju.edu.cn"
CAS_ORIGIN = "https://zjuam.zju.edu.cn"
LOGIN_ENTRY_URL = f"{COURSES_ORIGIN}/login"
PUBKEY_URL = f"{CAS_ORIGIN}/cas/v2/getPubKey"
KAPTCHA_URL = f"{CAS_ORIGIN}/cas/v2/getKaptchaStatus"
TODOS_URL = f"{COURSES_ORIGIN}/api/todos"

# 探测跑通用的是浏览器 UA；站方对裸 UA（python-urllib）会不会区别对待**没测过**，
# 所以沿用实测过的那一形状，不拿它当"必须"。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CREDENTIAL_USER_KEY = "ZJU_USERNAME"
CREDENTIAL_PASSWORD_KEY = "ZJU_PASSWORD"

_TIMEOUT_SECONDS = 25

#: 站方在"账密不对"那张页面上给的话术。**只拿来分格，不拿来当判据**：判据是业务端点
#: 拿没拿回 JSON（上面第 6 条）。认不出这句话就报 ``login_changed``，不冒充「密码错」。
_CREDENTIAL_ERROR_MARKER = "用户名或密码错误"
_LOGIN_FORM_MARKER = re.compile(r'<form id="fm1"[^>]*action="([^"]+)"')
_EXECUTION_MARKER = re.compile(r'name="execution"\s+value="([^"]*)"')
_UUID_MARKER = re.compile(r'id="uuid"\s+value="([^"]*)"')


class TransportError(RuntimeError):
    """连接层面的失败（DNS / TLS / 超时 / 拒绝）。**与"学校拒了这次登录"是两格**。"""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(f"网络不可达：{detail}")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    text: str

    def json(self) -> object:
        return json.loads(self.text)


class Transport(Protocol):
    """注入位。测试一律塞假的进来 —— 默认回退到真网络调用就是那个洞的形状（裁定 104 二）。"""

    def get(self, url: str) -> HttpResponse: ...

    def post_form(
        self, url: str, data: Mapping[str, str], *, referer: Optional[str] = None
    ) -> HttpResponse: ...


class UrllibTransport:
    """:class:`Transport` 的真身：一份 cookie jar + 跟着重定向，stdlib 而已（不引新依赖）。

    4xx/5xx 一律转成正常的 :class:`HttpResponse` 交回去 —— 站方的 403 是有信息的一格
    （``/api/user`` 那一条），把它当异常抛出来就会把"进去了"读成"掉线"。
    """

    def __init__(self, *, timeout: float = _TIMEOUT_SECONDS):
        import http.cookiejar

        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPRedirectHandler(),
        )
        opener.addheaders = [("User-Agent", USER_AGENT)]
        self._opener = opener

    def _send(self, request: urllib.request.Request) -> HttpResponse:
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read()
                return HttpResponse(
                    status=getattr(response, "status", None) or response.getcode(),
                    url=response.geturl(),
                    text=body.decode("utf-8", errors="replace"),
                )
        except urllib.error.HTTPError as exc:  # 4xx/5xx：是有内容的一格，不是断线
            body = exc.read() if hasattr(exc, "read") else b""
            return HttpResponse(
                status=exc.code, url=exc.url, text=body.decode("utf-8", errors="replace")
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(str(getattr(exc, "reason", None) or exc)) from None

    def get(self, url: str) -> HttpResponse:
        return self._send(urllib.request.Request(url, method="GET"))

    def post_form(
        self, url: str, data: Mapping[str, str], *, referer: Optional[str] = None
    ) -> HttpResponse:
        payload = urllib.parse.urlencode(dict(data)).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if referer:
            headers["Referer"] = referer
        return self._send(urllib.request.Request(url, data=payload, headers=headers, method="POST"))


def read_credentials() -> Optional[tuple[str, str]]:
    """只走产品那一条凭据真源读账密；拿不到就 ``None``（不猜、不回退到第二处）。

    真源 = 根 ``.env``（裁定 89 甲 / 裁定 110 二）：``credential_env_path()`` +
    ``get_env_value_prefer_dotenv()``。**不许在本包里自己 ``open('.env')`` 或读
    ``os.environ``** —— 抄第二条读路就是再造一份会不一致的"真源"。
    任何一条路上都不打印、不日志、不落盘这两个值。
    """
    from plobi_cli.config import get_env_value_prefer_dotenv

    username = (get_env_value_prefer_dotenv(CREDENTIAL_USER_KEY) or "").strip()
    password = (get_env_value_prefer_dotenv(CREDENTIAL_PASSWORD_KEY) or "").strip()
    if not username or not password:
        return None
    return username, password


def _bi_to_hex(value: int) -> str:
    """站方 ``biToHex``/``digitToHex`` 的形状：**每 16 位一组、每组写满 4 个十六进制字符**。

    ``format(x, "x")`` 不补前导零 ⇒ 顶部那一组带 0 时我们会少 1–3 个字符，而站方按 4 个一组
    往回解（``biFromHex``），错位的密文解出来的整数就不是原来那个 —— 症状是
    **「密码明明是对的，却报用户名或密码错误」，且每次登录约有 1/16 的概率撞上**。
    治理线那三次逐字节对照都是满 128 字符的样本（概率 15/16），所以这条一直没露头；
    本线换了一个假模数就撞出来了（对照 node 跑站方 ``security.js`` 复现，见交账）。
    """
    hex_text = format(value, "x")
    return hex_text.zfill(4 * ((len(hex_text) + 3) // 4))


def _utf16_code_units(text: str) -> list[int]:
    """JS 字符串那种"码元序列"：``s.charCodeAt(i)`` 取的是 UTF-16 码元，不是字节。

    ASCII 下码元＝字节，所以按 UTF-8 分块的写法在实测口令（纯 ASCII）上一直没露馅；
    口令里只要有一个非 ASCII 字符，UTF-8 会让分块位置和站方差一整截。这里按码元走，
    为的是**跟站方自己的前端发同一串**，而不是跟我们顺手的编码发。
    """
    raw = text.encode("utf-16-le")
    return [raw[index] | (raw[index + 1] << 8) for index in range(0, len(raw), 2)]


def _block_to_int(units: list[int]) -> int:
    """``block.digits[j] = a[k] + (a[k+1] << 8)`` —— 每两个码元拼一个 16 位 limb，小端。

    用的是**加**不是或：码元大于 255 时这两个式子不等价，而"limb 数组按 65536 进位"才是
    站方那个 BigInt 的字面形状。⚠️ 但站方的库在 limb 超过 16 位时并不归一化，算出来的
    **不是标准 RSA 结果** —— 我拿 node 跑它自己的 ``security.js`` 对过：ASCII 全等，
    含中文的假口令这一格对不上（这台没有这种口令，也没法替学校修它的库）。
    ⇒ 结论照实写：**非 ASCII 口令这一路未验证**，我们发的是"应该是对的那一串"；
    真撞上就按 :data:`~.status.STATE_AUTH_FAILED` 报，不许猜它登得进。
    """
    value = 0
    for limb_index in range(0, len(units), 2):
        low = units[limb_index]
        high = units[limb_index + 1] if limb_index + 1 < len(units) else 0
        value += (low + (high << 8)) * (65536 ** (limb_index // 2))
    return value


def encrypt_password(password: str, modulus_hex: str, exponent_hex: str) -> str:
    """站方 ``security.js`` 那套 RSA 字符串格式（逐字符对照过 node 跑它自己的 ``security.js``）。

    三处都别"顺手简化"，每一处都对着站方的形状：

    - **先反转**（``split("").reverse().join("")`` 在调用方做，与 :func:`_utf16_code_units` 同一条尺）；
    - **``chunkSize`` 由模数长度推出**（``2 * biHighIndex(m)``，512 位模数＝62 个**码元**），
      不写死 62 —— 写死就是等谁换模数谁踩；
    - **每块密文按 16 位一组写满 4 个十六进制字符**（:func:`_bi_to_hex`，那条 1/16 概率的雷）。

    ⚠️ 非 ASCII 口令这一条**没有实测**：站方的算法把码元当字节用（``a[k] + (a[k+1] << 8)``），
    码元大于 255 时那个 limb 会溢出，服务端解出什么没人验过。我们做的是"跟它自己的前端发同一串"，
    不是"保证这种口令登得进去"。撞上了就照 :data:`STATE_AUTH_FAILED` 报，别猜。
    """
    modulus = int(modulus_hex, 16)
    exponent = int(exponent_hex, 16)
    chunk_size = 2 * (len(modulus_hex) // 4 - 1)  # = 2 * biHighIndex(m)，单位是码元
    if chunk_size <= 0:
        raise ValueError(f"模数太短，推不出分块大小：{len(modulus_hex)} 个十六进制字符")

    units = _utf16_code_units(password)[::-1]
    while len(units) % chunk_size:
        units.append(0)

    parts = []
    for start in range(0, len(units), chunk_size):
        block = units[start : start + chunk_size]
        cipher = pow(_block_to_int(block), exponent, modulus)
        parts.append(_bi_to_hex(cipher))
    return " ".join(parts)


def _todo_rows(response: HttpResponse) -> Optional[list[dict]]:
    """业务端点给回的是不是那份清单 —— **这一条就是登录态的唯一判据**。"""
    if response.status != 200:
        return None
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    rows = payload.get("todo_list") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _safe_url(url: str) -> str:
    """只留 ``scheme://host/path`` —— 查询串与片段一律不进状态文件。

    CAS 那一串的跳转里带 ``ticket=ST-…``，那是**一次性的登录票据**；把 URL 原样写进
    ``zju_state.json``（再被面上读出来）就等于把票据落盘。调研件里上游刻意不留票据，
    我们这边至少不主动抄一份（`Docs/reference/sources-shape.md` §2「不可持久化」那一列）。
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _classify_unauthenticated(post_text: str) -> str:
    if _CREDENTIAL_ERROR_MARKER in post_text:
        return STATE_AUTH_FAILED
    return STATE_LOGIN_CHANGED


def authenticate(transport: Transport, username: str, password: str) -> tuple[str, str, list[dict]]:
    """走完登录链，返回 ``(state, detail, rows)``；``state == ready`` 时 rows 已拿到。

    失败不抛、不返空清单冒充成功 —— ``state`` 就是那一句"为什么没东西"（``status.py``）。
    """
    try:
        entry = transport.get(LOGIN_ENTRY_URL)
        action = _LOGIN_FORM_MARKER.search(entry.text)
        execution = _EXECUTION_MARKER.search(entry.text)
        if not action or not execution:
            # 两条路都得走一趟业务端点才能分开：会话还活着（站方把 /login 直接送回应用）
            # 与"站方改了页面"这两种回话长得不一样，但都**没有** fm1 表单。
            # 探测脚本当年就停在 `NO_FORM (already authed? or page changed)` 这一句上——
            # 那是不肯猜、留成两个可能的形状；这里用同一把尺把它分开，而不是合并成一格。
            probe = transport.get(TODOS_URL)
            rows = _todo_rows(probe)
            if rows is not None:
                return STATE_READY, _safe_url(probe.url), rows
            return (
                STATE_LOGIN_CHANGED,
                f"GET {_safe_url(entry.url)} 的 {entry.status} 回话里既没有登录表单，业务端点也不是作业清单",
                [],
            )

        action_url = htmllib.unescape(action.group(1))
        if action_url.startswith("/"):
            action_url = CAS_ORIGIN + action_url

        kaptcha = transport.get(KAPTCHA_URL)
        if kaptcha.text.strip().lower().startswith("true"):
            # 站方这次要人工验证码 —— 当场停手，不再 POST（多打一次就多送一次失败计数）。
            return STATE_CAPTCHA_REQUIRED, "getKaptchaStatus=true", []

        key = transport.get(PUBKEY_URL)
        try:
            pubkey = key.json()
            cipher = encrypt_password(password, str(pubkey["modulus"]), str(pubkey["exponent"]))
        except (ValueError, KeyError, TypeError) as exc:
            return STATE_LOGIN_CHANGED, f"公钥接口形状对不上（{type(exc).__name__}: {exc}）", []

        uuid_value = _UUID_MARKER.search(entry.text)
        posted = transport.post_form(
            action_url,
            {
                "authcode": "",
                "execution": execution.group(1),
                "password": cipher,
                "rememberMe": "true",
                "username": username,
                "_eventId": "submit",
                "uuid": uuid_value.group(1) if uuid_value else "",
                "ys": "5",
            },
            referer=entry.url,
        )
        # POST 那张页面本身不算数（成功时它可能是应用首页 HTML，失败时它是带错误话的登录页）：
        # **成不成只认业务端点**，所以这里直接打 /api/todos，再按需要分格。
        probe = transport.get(TODOS_URL)
        rows = _todo_rows(probe)
        if rows is not None:
            return STATE_READY, _safe_url(probe.url), rows
        return _classify_unauthenticated(posted.text), _safe_url(posted.url), []
    except TransportError as exc:
        # 网络这一格**必须单独报**，不许并进"认证失败"：这台可达性会随代理/TUN 翻面。
        return STATE_NETWORK_UNREACHABLE, exc.detail, []


def fetch_todos(transport: Transport) -> tuple[str, str, list[dict]]:
    """只打业务端点（已登录会话用）。拿不到清单就给出"为什么"那一格。"""
    try:
        response = transport.get(TODOS_URL)
    except TransportError as exc:
        return STATE_NETWORK_UNREACHABLE, exc.detail, []
    rows = _todo_rows(response)
    if rows is not None:
        return STATE_READY, _safe_url(response.url), rows
    # 会话掉了与站方改了接口在这一格分不开：先按最保守的读法报「登录态不成」，
    # 由调用方重登一次再看 —— 重登之后仍认不出回话就是 login_changed。
    return STATE_AUTH_FAILED, f"{response.status} {_safe_url(response.url)}", []

"""幕布那三家接口的**唯一一只**请求手（2026-10-27 这台实测通的形状）。

形状是从它自己的安装包（``app.asar``）里核出来的，不是猜的：

- 域名是 ``api2.mubu.com``，不是 ``mubu.com``——后者对这些路径回一段 HTML。
  （在 ``main-*.js`` 里 ``t.apiHost="https://api2.mubu.com"``，``/v3/api``、``/v4/api``、
  ``/convert`` 三类前缀都走它。）
- 认证是**请求头** ``jwt-token``，不是 cookie（``normalizeAuthHeaders`` 那一只手同时写
  ``Jwt-Token`` 与 ``jwt-token``）。
- 桌面侧的默认头是 ``mubu-desktop: true`` + ``platform`` + ``platform-version`` + 一个
  ``… Mubu Electron`` 的 UA。这四个头**不是装饰**：同一枚 token、同一个 ``{start:0}``，
  只带 ``jwt-token`` 时 ``get_all_documents_page`` 回 ``5001 system error``，
  补上这四个头才回数据。
- 分页参数是 ``{offset, limit}``，**不是** ``{start}``/``{count}``/``{size}``（那三个都 ``5001``）。

这一只手把"接口说不通"统一收成 :class:`MubuError`，代码词留在里面，给人看的那句由
采集器与界面各自去说（屏上不出现 ``5001``）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

API_HOST = "https://api2.mubu.com"
#: 从它自己安装包里读到的版本号；只是 UA 的一格，不参与判据。
DESKTOP_VERSION = "6.1.0"

_ALL_DOCUMENTS = "/v3/api/list/get_all_documents_page"
_FOLDER = "/v3/api/list/get"
_DOCUMENT = "/v3/api/document/get"
_PROFILE = "/v3/api/user/profile"

#: 接口自己说的"这一条你读不到"那些码。登录态失效那一族今天没实测到具体数字，
#: 所以这里**只登记实测见过的**，其余一律按"接口回了个非零码"处理，不假装认识。
_NOT_AUTHENTICATED_HINTS = (4001, 4003, 401)


class MubuError(RuntimeError):
    """这条路今天走不通。``code`` 是给人归因用的词表（``network_unreachable`` / ``auth_failed``…）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _headers(token: str) -> dict[str, str]:
    return {
        "content-type": "application/json",
        "jwt-token": token,
        "mubu-desktop": "true",
        "platform": "windows",
        "platform-version": DESKTOP_VERSION,
        "User-Agent": f"windows Mubu Electron {DESKTOP_VERSION}",
    }


class MubuClient:
    """四只口：`profile` / `document_ids` / `folder` / `document`。全部 POST，全部只读。"""

    def __init__(self, token: str, *, host: str = API_HOST,
                 transport: Optional[Callable[[str, dict, dict], dict]] = None,
                 timeout: int = 25) -> None:
        self.token = token
        self.host = host
        self.timeout = timeout
        self._transport = transport or self._urllib

    def _urllib(self, url: str, payload: dict, headers: dict) -> dict:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise MubuError("http_error", f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MubuError("network_unreachable", type(exc).__name__) from exc

    def post(self, path: str, payload: dict) -> Any:
        """一次调用；`code` 非零就抛，零就把 `data` 交出去。"""
        body = self._transport(self.host + path, payload, _headers(self.token))
        if not isinstance(body, dict):
            raise MubuError("parse_failed", type(body).__name__)
        code = body.get("code")
        if code != 0:
            number = code if isinstance(code, int) else -1
            if number in _NOT_AUTHENTICATED_HINTS:
                raise MubuError("auth_failed", str(body.get("msg") or code))
            raise MubuError("api_error", f"{code} {str(body.get('msg') or '')[:40]}".strip())
        return body.get("data")

    def profile(self) -> dict:
        data = self.post(_PROFILE, {})
        return data if isinstance(data, dict) else {}

    def document_ids(self, *, limit: int = 500) -> list[dict]:
        """全量名单的第一层：``root_relation`` 是一串 JSON 文本，元素 ``{id, type}``。"""
        data = self.post(_ALL_DOCUMENTS, {"offset": 0, "limit": limit})
        relation = (data or {}).get("root_relation") if isinstance(data, dict) else None
        if isinstance(relation, str):
            try:
                relation = json.loads(relation)
            except json.JSONDecodeError:
                raise MubuError("parse_failed", "root_relation") from None
        return [row for row in (relation or []) if isinstance(row, dict) and row.get("id")]

    def folder(self, folder_id: str) -> dict:
        """一个目录里的 ``documents`` 与 ``folders``（根那一层只回目录，所以要逐层走）。

        请求体**只带 ``folderId``**：这台实测多带 ``sort``/``keywords``/``source`` 里的任何
        一个（``source: 0`` 也算）就回一整套空表——是它服务端按参数存在与否分支，不是我们传空值就行。
        """
        data = self.post(_FOLDER, {"folderId": folder_id})
        rows = data if isinstance(data, dict) else {}
        return {"documents": rows.get("documents") or [], "folders": rows.get("folders") or []}

    def document(self, doc_id: str) -> dict:
        """一篇的正文在这里：``{name, definition, baseVersion, directory, role, author}``。"""
        data = self.post(_DOCUMENT, {"docId": doc_id})
        return data if isinstance(data, dict) else {}


__all__ = ["API_HOST", "MubuClient", "MubuError"]

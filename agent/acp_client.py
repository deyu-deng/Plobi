"""ACP 的**协议那一层**：一个 ACP server 进程 ＋ 它那套 JSON-RPC（stdio）。

ACP（Agent Client Protocol，agentclientprotocol）是一套**一行一个 JSON** 的
JSON-RPC，跑在子进程的标准输入／标准输出上。本模块只管协议：

* 发请求、等答复（:meth:`AcpClient.request`）；
* 收它主动发来的通知（``session/update``：它干活时一段一段回话）；
* **答它的授权问句**（``session/request_permission``）——**不回它，它就卡在那儿等**。

**协议方法名**（现读本机那份官方 SDK 的 schema：``@agentclientprotocol/sdk``
的 ``schema/v1/schema.json``，2026-11-15）：

===========================  ==========  ==================================
方法                          谁发起       干什么
===========================  ==========  ==================================
``initialize``                客户端       报上协议版本与客户端能力
``session/new``               客户端       开一条会话（答复里带它自己的档）
``session/set_mode``          客户端       切它那一档（答复只有 ``_meta``）
``session/prompt``            客户端       把活交出去（答复带 ``stopReason``）
``session/update``            厂商         通知（``agent_message_chunk`` 是它说的话）
``session/request_permission``  厂商        要授权（工具调用前问一句）
===========================  ==========  ==================================

**这一层不含任何策略**：档怎么选、授权怎么答，都在上一层
（``agent/external_offload.py::run_acp_task``）——策略不许埋进传输里。
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import threading
import time
from typing import Any, Optional

#: ACP 协议版本（现读官方 SDK 的 v1 schema）。
PROTOCOL_VERSION = 1

#: 客户端自报家门（它会记进日志；**不写机器路径**）。
CLIENT_INFO = {"name": "plobi-offload", "version": "1"}

#: 客户端能力：**不**声明文件读写（声明了它就会来要 fs/*，那一支我们没实现）。
CLIENT_CAPABILITIES = {"fs": {"readTextFile": False, "writeTextFile": False}}

#: 授权问句里「准」的那几档（先一次性的，再记久的）。
ALLOW_KINDS = ("allow_once", "allow_always")
#: 授权问句里「不准」的那几档。
REJECT_KINDS = ("reject_once", "reject_always")


class AcpError(RuntimeError):
    """协议层的一次失败：厂商拒了、没回话、或者回了错误。"""


def error_text(err: Any) -> str:
    """把厂商给的那个错误对象说成人话——**它自己的话**，我们不替它翻译成「失败了」。

    ACP 的错误有两种放法：``{"code":…,"message":…}``，与
    ``{"code":…,"message":…,"data":{"details":…}}``（11-15 实测 GLM 的
    「No API key found」就在 ``data.details`` 里——只取 ``message`` 会把
    真正有用的那半句丢掉）。
    """
    if not isinstance(err, dict):
        return str(err or "")
    message = str(err.get("message") or "")
    code = err.get("code")
    data = err.get("data")
    detail = ""
    if isinstance(data, dict):
        detail = str(data.get("details") or data.get("message") or "")
    elif isinstance(data, str):
        detail = data.strip()
    parts = [part for part in (message, detail) if part]
    body = "：".join(parts) if parts else ""
    return f"{body}（code {code}）" if code is not None else body


class AcpClient:
    """一个 ACP server 进程 ＋ 它那套 JSON-RPC。

    **用法**：:meth:`start` → 一串 :meth:`request` → :meth:`close`。
    ``close`` 杀的是**整棵树**（只杀外壳的话，孙进程还攥着管道不放）。
    """

    def __init__(self, argv, *, cwd: str, env: dict[str, str]) -> None:
        self.argv = [str(part) for part in argv]
        self.cwd = str(cwd)
        self.env = dict(env)
        self.proc: Optional[subprocess.Popen] = None
        #: 它发来的通知（``session/update`` 等），按到达次序存着。
        self.updates: list[dict[str, Any]] = []
        #: 它的 stderr（留个底：它起不来时那儿有话）。
        self.stderr_lines: list[str] = []
        #: 授权问句**答不答准**（策略由上层按那一档定，传输层不自己决定）。
        self.allow_permissions = True
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0

    # ── 进程 ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """起那台 server。起不来 ⇒ 抛 :class:`AcpError`（**不吞**：没起来就是没起起来）。"""
        try:
            self.proc = subprocess.Popen(  # noqa: S603 — argv 是真源表那张表拼出来的
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise AcpError(f"起不来 {self.argv[0]!r}：{exc}") from exc
        threading.Thread(target=self._read_stdout, args=(self.proc.stdout,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc.stderr,), daemon=True).start()

    def _read_stdout(self, stream) -> None:
        try:
            for line in stream:
                self._lines.put(line)
        except Exception:  # noqa: BLE001 - 管道读完了就是读完了
            pass
        self._lines.put(None)

    def _read_stderr(self, stream) -> None:
        try:
            for line in stream:
                self.stderr_lines.append(line.rstrip())
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> str:
        """收摊：杀**整棵树**，回一句人能读的读数（杀不掉就照实说）。"""
        note = ""
        if self.proc is not None:
            with contextlib.suppress(Exception):
                note = _kill_process_tree(self.proc)
            with contextlib.suppress(OSError):
                self.proc.kill()
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=5)
        return note

    # ── 传输 ─────────────────────────────────────────────────────────────

    def _write(self, payload: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise AcpError("进程还没起（或者已经收了）：写不出去。")
        try:
            self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n"))
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise AcpError(f"写不出去：{exc}") from exc

    def request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        """发一个请求，等它的**答复**。

        返回 ``{"result": …}`` 或 ``{"error": …}``（**不替它判断好坏**——
        「拒了」也是一条读数，由上一层说成人话）。
        """
        self._next_id += 1
        ident = self._next_id
        self._write({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        reply = self._pump(ident, time.monotonic() + timeout)
        if reply is None:
            raise AcpError(f"{method}：它没回话（{timeout:.0f}s 内）。")
        return reply

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """发一个通知（没有答复的那种）。"""
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _pump(self, want_id: int, deadline: float) -> Optional[dict[str, Any]]:
        """读到 ``want_id`` 的答复为止；顺带收通知、答授权问句。"""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if line is None:
                return None  # 它的 stdout 完了 ⇒ 不会再有答复
            text = str(line).strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue  # 非 JSON 的前言噪声（有些 CLI 先打一行）⇒ 跳过
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg
            method = msg.get("method")
            if method and "id" in msg:
                # 它主动要东西（session/request_permission）⇒ **必须答**，否则它卡住。
                self._answer(msg)
                continue
            if method:
                self.updates.append(msg)

    def _answer(self, msg: dict[str, Any]) -> None:
        """答它的授权问句。

        **答法**（策略层给的是「准」还是「不准」）：挑一个对应的选项；
        没有对应选项就答「取消」——**不替主人点同意，也不替主人点拒绝**，
        只在策略明确允许的那一边选。
        """
        ident = msg.get("id")
        params = msg.get("params") or {}
        options = params.get("options") or []
        kinds = ALLOW_KINDS if self.allow_permissions else REJECT_KINDS
        chosen = ""
        for kind in kinds:
            for option in options:
                if isinstance(option, dict) and str(option.get("kind") or "") == kind:
                    chosen = str(option.get("optionId") or "")
                    break
            if chosen:
                break
        result = ({"outcome": {"outcome": "selected", "optionId": chosen}}
                  if chosen else {"outcome": {"outcome": "cancelled"}})
        with contextlib.suppress(AcpError):
            self._write({"jsonrpc": "2.0", "id": ident, "result": result})

    # ── 它说过的话 ───────────────────────────────────────────────────────

    def agent_text(self) -> str:
        """它说过的话（``agent_message_chunk`` 拼起来）。

        **只有 ``agent_message_chunk`` 算它说的话**：``agent_thought_chunk``
        是它心里想的（不该进台账），``tool_call`` 是它在报告自己动手了。
        """
        parts: list[str] = []
        for msg in self.updates:
            params = msg.get("params") or {}
            update = params.get("update") or {}
            if not isinstance(update, dict):
                continue
            if str(update.get("sessionUpdate") or "") != "agent_message_chunk":
                continue
            content = update.get("content") or {}
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts).strip()

    def tool_calls(self) -> list[dict[str, Any]]:
        """它动过的手（``tool_call`` 通知）——给日志与台账看，不上屏。"""
        out: list[dict[str, Any]] = []
        for msg in self.updates:
            params = msg.get("params") or {}
            update = params.get("update") or {}
            if isinstance(update, dict) and str(update.get("sessionUpdate") or "") == "tool_call":
                out.append(update)
        return out


def _kill_process_tree(proc: Any) -> str:
    """杀掉一个子进程的**整棵树**（复用通道那一支的杀法，那儿是本机实测过的）。

    只杀外壳的话，孙进程攥着管道继续跑（11-08 实测 qoder 报 180s、实际跑了 499s）。
    """
    from agent import external_offload as channel

    try:
        return channel._kill_descendants(proc)
    except Exception:  # noqa: BLE001 - 杀不掉不是错误状态，收摊照样收
        return ""

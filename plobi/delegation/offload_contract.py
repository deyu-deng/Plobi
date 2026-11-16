"""外包这一族的**统一契约表**，以及 ACP 那一腿的登录态探测。

**为什么有这一张表（用户 11-14：「连带把「每家一份适配」改成统一契约」）**

加一家外部软件，这一族原先要动**四个地方**，每处都是硬编码：

1. 通道那张表 ``agent/external_offload.py::EXTERNAL_AGENTS``（怎么起它、怎么放开权限）；
2. 登录证据 ``tools/offload_tools.py::_LOGIN_EVIDENCE``（凭据落在哪个文件／环境变量）；
3. 回跳旗标 ``plobi/console/router.py::OFFLOAD_RESUME_ARGV``（``{session_id}`` 那一枚）；
4. 它自己的 ACP 入口——**这一处原先根本不在仓里**，谁要用只能去翻官方注册表。

漏一处就是「这家悄悄用了那家的旗标」。本模块把**散在别处的那几样**收成一家一行：
哪一家在官方 ACP 注册表里叫什么、起 ACP 模式要哪几枚旗标、能不能回跳、它的会话放在哪。
通道那张表本来就在一处（``EXTERNAL_AGENTS``），本模块按 id 与它对齐，**不重复它已经有的事实**。

**登录态为什么问 ACP（11-14 本机现核）**

凭据文件只是**痕迹**：``~/.gemini/settings.json`` 里写着 ``oauth-personal`` 不等于此刻还能开会话。
官方 ACP 注册表（``agentclientprotocol/registry``，Apache-2.0）里每家都怎么起 ACP 模式是公开的，
而 ``session/new`` 那一跳的答复**是那家软件自己给的**——它说 ``-32000 Authentication required``
就是没登录，它给出会话号就是登录着。

11-14 在这台机器上逐家实测（每家一次握手，不发提示词、不烧额度）：

===============  ===================  ===============================
家               起 ACP 的旗标          握手答复
===============  ===================  ===============================
codebuddy        ``--acp``             给了会话 ⇒ **登录着**
cline            ``--acp``             给了会话 ⇒ **登录着**
copilot          ``--acp``             给了会话 ⇒ **登录着**
kilo             ``acp``               给了会话 ⇒ **登录着**
opencode         ``acp``               给了会话 ⇒ **登录着**
kiro             ``acp``               给了会话 ⇒ **登录着**
gemini           ``--acp``             「Gemini API key is missing or not configured.」
qwen             ``--acp``             「Authentication required: Use Qwen Code CLI to authenticate first.」
qoder            ``--acp``             12s 没回话（它起得慢）⇒ 探不到
agy              ``acp``               没回话 ⇒ 探不到
codex / claude   ——                    官方注册表里没有这一家
===============  ===================  ===============================

**三态，不是两态**（这一族的老规矩：读不出的不许写成「不能」）：

* ``True``  —— 它自己给我开了一条会话；
* ``False`` —— 它按 ACP 的规矩拒了（``-32000``／``authentication required``／``call authenticate``），
  **那是「它自己说没登录」**，不是我们猜的；
* ``None``  —— 探不到：没回话、拒的原因不是登录、官方注册表里没有它、本机没装它。

⚠️ **一条不许越过的界**：ACP 那一腿拒了，**不等于**那家软件没登录——gemini 的 ACP 腿要 API key，
而它自己的 OAuth 登录可能好着。所以**只有标准那种拒（认证类措辞）才判 ``False``**，
其余一律 ``None``（探不到）。宁可说「探不到」，也不把「这一腿没给我开会话」说成「它没登录」。
"""

from __future__ import annotations

import dataclasses
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from typing import Any, Optional

#: ACP 的认证错误码（协议里那一枚，厂商也拿它当通用错误码用——所以本模块**不**按它判登录，
#: 见 :func:`_looks_like_auth_refusal`）。
ACP_AUTH_ERROR_CODE = -32000

#: 认证类措辞。与额度线那一支（``bezel/bezel/acp.py``）同一口径。
_AUTH_MARKERS = ("authentication required", "call authenticate", "not authenticated", "not logged in")

#: 一次握手给多少秒。实测 qwen 3.0s、cline 2.4s、kiro 8.4s、copilot 10.5s、qoder 12s 没回话。
DEFAULT_TIMEOUT_SECONDS = 12.0

#: 探测答案留多久。登录是**会变**的事（人刚登进去、或 token 过期了），所以这个值是短的；
#: 但也不能短到每次开屏都重跑一遍握手（每家 2–12s）。
CACHE_TTL_SECONDS = 1800.0

#: 契约表本体在**真源表**里（``agent/vendor_registry.py``：一行一家，四张出口表都由它派生）。
#: 这里只 re-export —— 调用方（``plobi/console/router.py``、``tools/offload_tools.py``…）
#: 认的还是 ``plobi.delegation.offload_contract.OFFLOAD_CONTRACT``，公共形状一个字不改。
from agent.vendor_registry import (  # noqa: E402  （re-export：公共形状不许动）
    NO_RESUME_FLAG,
    OFFLOAD_CONTRACT,
    VendorContract,
    resume_argv_map as _resume_argv_map,
)


def contract_for(vendor: str) -> Optional[VendorContract]:
    """认不出的一家 ⇒ ``None``，由调用方决定怎么报——不猜一个默认的。"""
    return OFFLOAD_CONTRACT.get((vendor or "").strip().lower())


def resume_argv_map() -> dict[str, tuple[str, ...]]:
    """回跳旗标那张表：**只有实测过的那几家**才有。

    没实测过的旗标不许照着别家的形状编一个——那会让主人点一下跳到别的地方去。
    真源在 ``agent/vendor_registry.py``（一行一家），这里只是它的一张派生表。
    """
    return _resume_argv_map()


@dataclasses.dataclass(frozen=True)
class AcpLoginAnswer:
    """一次 ACP 握手的读数。

    ``value`` 三态：``True`` 它给我开了会话；``False`` 它按认证的规矩拒了（**它自己说的**）；
    ``None`` 探不到。``source`` 与 ``detail`` 是给日志与 tooltip 看的，不上屏。
    """

    vendor: str
    value: Optional[bool]
    source: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _cache_path() -> str:
    """探测答案落在数据家里（裁定 48.1：不许写死盘符与家目录）。"""
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home()
    except Exception:  # noqa: BLE001
        root = os.path.join(tempfile.gettempdir(), "plobi")
    return os.path.join(root, "offload", "acp_login.json")


def _load_cache() -> dict[str, dict[str, Any]]:
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    now = time.time()
    out: dict[str, dict[str, Any]] = {}
    for vendor, entry in stored.items():
        if not isinstance(entry, dict):
            continue
        try:
            stamp = float(entry.get("probed_at") or 0)
        except (TypeError, ValueError):
            continue
        if now - stamp >= CACHE_TTL_SECONDS:
            continue
        out[str(vendor)] = entry
    return out


def _save_cache(entries: dict[str, dict[str, Any]]) -> None:
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        # 写不进去不是错误：这一格退回「没探到」，不挡屏。
        pass


def cached_answer(vendor: str) -> Optional[AcpLoginAnswer]:
    """上一次探到的答案（还在有效期内）。**不探**。"""
    entry = _load_cache().get((vendor or "").strip().lower())
    if not entry:
        return None
    value = entry.get("value")
    return AcpLoginAnswer(
        vendor=(vendor or "").strip().lower(),
        value=True if value is True else (False if value is False else None),
        source=str(entry.get("source") or "acp"),
        detail=str(entry.get("detail") or ""),
    )


def cached_answers(vendors: Optional[list[str]] = None) -> dict[str, AcpLoginAnswer]:
    """上一次探到的那些答案（还在有效期内）。**不探**。一家读一次盘。"""
    entries = _load_cache()
    names = list(vendors if vendors is not None else OFFLOAD_CONTRACT.keys())
    out: dict[str, AcpLoginAnswer] = {}
    for vendor in names:
        key = (vendor or "").strip().lower()
        entry = entries.get(key)
        if not entry:
            continue
        value = entry.get("value")
        out[key] = AcpLoginAnswer(
            vendor=key,
            value=True if value is True else (False if value is False else None),
            source=str(entry.get("source") or "acp"),
            detail=str(entry.get("detail") or ""),
        )
    return out


def _remember(answer: AcpLoginAnswer) -> AcpLoginAnswer:
    entries = _load_cache()
    entries[answer.vendor] = {
        "value": answer.value,
        "source": answer.source,
        "detail": answer.detail,
        "probed_at": time.time(),
    }
    _save_cache(entries)
    return answer


def _looks_like_auth_refusal(err: Any) -> bool:
    """这一次拒**是不是认证类**的那一种。

    **只认证词那一族措辞**，不看错误码。理由：厂商把 ``-32000`` 当通用错误码用，
    gemini 11-14 实测就是 ``-32000 Gemini API key is missing or not configured.``
    ——那是「这条 ACP 腿要 API key」，**不是**「那家软件没登录」（它自己的 OAuth 登录
    可能好着）。把它说成「没登录」就是在说假话。

    与额度线那一支（``bezel/bezel/acp.py``）同一口径。
    """
    if not isinstance(err, dict):
        return False
    text = json.dumps(err, ensure_ascii=False).lower()
    return any(marker in text for marker in _AUTH_MARKERS)


def _error_message(err: Any) -> str:
    if isinstance(err, dict):
        message = str(err.get("message") or "")
        code = err.get("code")
        return f"{message}（code {code}）" if code is not None else message
    return str(err or "")


def _read_lines(pipe, sink: queue.Queue) -> None:
    try:
        for line in pipe:
            sink.put(line)
    except Exception:  # noqa: BLE001
        sink.put({"__error__": "管道读完了"})


def _await_reply(lines: queue.Queue, want_id: int, deadline: float) -> Optional[dict]:
    """等 ``want_id`` 的答复；通知与横幅噪声跳过（这些 CLI 会先打一行非 JSON 的前言）。"""
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            item = lines.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue
        if isinstance(item, dict):  # 读管道时出的错
            continue
        text = item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item)
        text = text.strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg
    return None


def _send(proc, message_id: int, method: str, params: dict) -> None:
    payload = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    proc.stdin.flush()


def probe_acp_login(
    vendor: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
) -> AcpLoginAnswer:
    """问那家软件自己：``session/new`` 给不给会话。

    **不发提示词**：握手只开一条空会话，不烧额度。**不抛**：起不来、没回话、拒了，
    都成一个读数（屏上只分「登录着／它自己说没登录／探不到」三态）。

    通道怎么起这台机器只有一份答案（``agent/external_offload.py``：``resolve_command``
    与 ``spawn_argv``）——这里不另起一套（裁定 94 族）。
    """
    from agent import external_offload as channel

    key = (vendor or "").strip().lower()
    spec = contract_for(key)
    if spec is None:
        return AcpLoginAnswer(key, None, "no-contract", "名册里没有这一家。")
    if not spec.acp_args and "acp" not in spec.legs:
        # 「有 ACP 入口」＝有旗标 **或** 它的派活腿就是 ACP（11-16）：
        # glm 那台 ACP server 不接旗标，但入口命令就是它自己。
        return AcpLoginAnswer(key, None, "no-acp", spec.acp_note or "这一家没法用 ACP 问。")

    if use_cache:
        hit = cached_answer(key)
        if hit is not None:
            return hit

    binary = channel.resolve_command(key)
    if not binary:
        return AcpLoginAnswer(key, None, "not-installed", "本机没装它（Plobi 不代下载）。")

    argv = channel.spawn_argv([binary, *spec.acp_args])
    proc = None
    lines: queue.Queue = queue.Queue()
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv 由名册那张表拼出来
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=os.environ.copy(),
        )
        assert proc.stdin is not None and proc.stdout is not None
        threading.Thread(target=_read_lines, args=(proc.stdout, lines), daemon=True).start()
        if proc.stderr is not None:
            threading.Thread(target=_read_lines, args=(proc.stderr, lines), daemon=True).start()

        deadline = time.monotonic() + timeout
        _send(proc, 1, "initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
            "clientInfo": {"name": "plobi-offload-probe", "version": "1"},
        })
        init = _await_reply(lines, 1, deadline)
        if init is None:
            return _remember(AcpLoginAnswer(
                key, None, "acp-silent",
                "它没回话（起得慢或不是 ACP 腿）⇒ 探不到。",
            ))
        if init.get("error"):
            err = init["error"]
            return _remember(AcpLoginAnswer(
                key,
                False if _looks_like_auth_refusal(err) else None,
                "acp-refused",
                f"initialize 被拒：{_error_message(err)}",
            ))

        _send(proc, 2, "session/new", {"cwd": tempfile.gettempdir(), "mcpServers": []})
        session = _await_reply(lines, 2, deadline)
        if session is None:
            return _remember(AcpLoginAnswer(
                key, None, "acp-silent",
                "它没回话 ⇒ 探不到（这一家起得慢，11-14 实测 qoder 12s、agy 15s 都没回）。",
            ))
        if session.get("error"):
            err = session["error"]
            auth = _looks_like_auth_refusal(err)
            return _remember(AcpLoginAnswer(
                key,
                False if auth else None,
                "acp-refused" if auth else "acp-refused-other",
                _error_message(err),
            ))

        return _remember(AcpLoginAnswer(
            key, True, "acp",
            "它自己的 ACP 腿给我开了一条会话 ⇒ 登录着。",
        ))
    except (OSError, ValueError, AssertionError) as exc:
        return _remember(AcpLoginAnswer(
            key, None, "acp-error", f"{type(exc).__name__}: {exc}"[:200],
        ))
    finally:
        if proc is not None:
            try:
                # 杀**整棵树**：只杀外壳的话，孙进程攥着管道继续跑（11-08 实测 qoder 跑了 499s）。
                channel._kill_descendants(proc)
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass


def probe_all(
    vendors: Optional[list[str]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
    max_workers: int = 4,
    deadline_seconds: Optional[float] = None,
) -> dict[str, AcpLoginAnswer]:
    """把该探的几家挨个探一遍（并行，最多 ``max_workers`` 个同时）。

    **只在用户主动要的时候**跑（首屏不许跑探针——这一屏的取数要在秒级出）。

    ``deadline_seconds`` 是**整批**的时限：到点还没答的那几家记成「探不到」，
    不让一次刷新把屏上那一格挂住。超时的那一家**不写缓存**（它这一次的沉默不是答案）。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    names = [n for n in (vendors if vendors is not None else OFFLOAD_CONTRACT.keys())]
    out: dict[str, AcpLoginAnswer] = {}
    if not names:
        return out

    def _one(name: str) -> AcpLoginAnswer:
        return probe_acp_login(name, timeout=timeout, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {pool.submit(_one, name): name for name in names}
        try:
            for future in futures:
                name = futures[future]
                try:
                    out[name] = future.result(timeout=deadline_seconds)
                except FutureTimeout:
                    out[name] = AcpLoginAnswer(name, None, "acp-deadline", "这一批探测到时限了 ⇒ 探不到。")
                except Exception as exc:  # noqa: BLE001
                    out[name] = AcpLoginAnswer(name, None, "acp-error", f"{type(exc).__name__}: {exc}"[:160])
        finally:
            for future in futures:
                future.cancel()
    return out

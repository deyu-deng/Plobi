"""Ask an installed ACP CLI, over its own protocol, two honest questions.

  1. **Are you signed in?**  `initialize` answers regardless of auth; it is
     `session/new` that refuses an agent with no credential (measured on this
     machine 2026-10-26: gemini-cli 0.65.0 / qwen-code 0.26.0 / cline 3.0.73 all
     complete the handshake and then answer `-32000` with an "Authentication
     required" message).
  2. **Which models do you actually have?**  `session/new` returns the agent's own
     `models.availableModels`, so the model names in Plobi's picker come from the
     CLI rather than from a list somebody hand-copied into a plugin file.

WHY THIS EXISTS. Two defects the owner reported (2026-10-27) both trace to us
answering these questions from guesswork instead of from the CLI:

  * the picker showed models whose **names were hand-seeded** and drift the first
    time the CLI upgrades;
  * a provider counted as "configured" as soon as its **binary resolved on PATH**,
    so an installed-but-never-signed-in CLI put its seeded models on screen, and
    picking one failed with an auth error.

So: no login, no models, no card in the picker. `logged_in=False` is the honest
answer and the caller must treat it as "not configured yet", not as "broken".

This module never sends a prompt: a probe must not spend the user's quota.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass

#: How long a probe answer stays trustworthy. A sign-in can happen while Plobi is
#: open (that is what the settings 连接 button does), so this is short on purpose —
#: the caller also invalidates it explicitly after a connect.
CACHE_TTL_SECONDS = 120.0

#: The whole exchange is two round trips on a local process. Anything slower than
#: this is a CLI that is not going to answer, and a hanging settings screen is
#: worse than an unknown one.
DEFAULT_TIMEOUT_SECONDS = 20.0

_AUTH_MARKERS = ("authentication required", "auth", "not logged in", "sign in", "login")

_CACHE: dict[str, tuple[float, AcpSessionInfo]] = {}
_CACHE_LOCK = threading.Lock()
_DISK_LOADED = False


def _cache_path() -> str:
    """Where probe answers survive a restart.

    The picker asks this question on every paint, and one honest answer costs up to
    ~25s (measured: qwen-code 0.26.0 loads a 24k-token context before it replies).
    Persisting turns "cold start" into a one-off background cost instead of a frozen
    settings screen.
    """
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home()
    except Exception:  # noqa: BLE001
        root = os.path.join(tempfile.gettempdir(), "plobi")
    return os.path.join(root, "acp_login_cache.json")


def _load_disk_cache() -> None:
    global _DISK_LOADED
    if _DISK_LOADED:
        return
    _DISK_LOADED = True
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
    except Exception:  # noqa: BLE001 — no cache yet is normal
        return
    now = time.time()
    with _CACHE_LOCK:
        for key, entry in (stored or {}).items():
            try:
                stamp = float(entry.get("probed_at") or 0)
                if now - stamp >= CACHE_TTL_SECONDS:
                    continue
                _CACHE[key] = (
                    stamp,
                    AcpSessionInfo(
                        installed=bool(entry.get("installed")),
                        logged_in=bool(entry.get("logged_in")),
                        models=tuple(str(m) for m in (entry.get("models") or [])),
                        current_model=str(entry.get("current_model") or ""),
                        detail=str(entry.get("detail") or ""),
                        probed_at=stamp,
                    ),
                )
            except Exception:  # noqa: BLE001
                continue


def _write_disk_cache() -> None:
    try:
        with _CACHE_LOCK:
            payload = {
                key: {
                    "installed": info.installed,
                    "logged_in": info.logged_in,
                    "models": list(info.models),
                    "current_model": info.current_model,
                    "detail": info.detail,
                    "probed_at": info.probed_at,
                }
                for key, (_, info) in _CACHE.items()
            }
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — a cache that cannot be written is not an error
        pass


@dataclass(frozen=True)
class AcpSessionInfo:
    """What one CLI said about itself. `detail` is for logs/tooltips, never body copy."""

    installed: bool
    logged_in: bool
    models: tuple[str, ...] = ()
    current_model: str = ""
    detail: str = ""
    probed_at: float = 0.0

    @property
    def usable(self) -> bool:
        """The one question the model picker asks: may this be offered at all?"""
        return self.installed and self.logged_in and bool(self.models)


def resolve_command(command: str) -> str:
    """Absolute path to an ACP CLI, or "".

    Windows again decides the shape: npm installs these as ``foo.CMD`` and
    CreateProcess only appends ``.exe``, so a bare name cannot be spawned
    (measured 2026-10-26). ``shutil.which`` finds it; pass that along.
    """
    raw = (command or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw) or "/" in raw or "\\" in raw:
        return raw if os.path.exists(raw) else ""
    return shutil.which(raw) or ""


def _read_lines(pipe, sink: queue.Queue) -> None:
    try:
        for line in pipe:
            sink.put(line)
    except Exception as exc:  # noqa: BLE001
        sink.put({"__error__": str(exc)})


def _await_reply(lines: queue.Queue, want_id: int, deadline: float) -> dict | None:
    """Wait for the response to `want_id`, ignoring notifications and banner noise.

    These CLIs print non-JSON preamble on stdout (Cline: "[acp] starting ACP mode
    over stdio…"), so a line that does not parse is skipped, not fatal.
    """
    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            item = lines.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue
        if isinstance(item, dict):  # reader error
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


def _looks_like_auth_refusal(message: str) -> bool:
    lowered = (message or "").lower()
    return any(marker in lowered for marker in _AUTH_MARKERS)


def _model_ids(result: dict) -> tuple[str, ...]:
    models = (result or {}).get("models") or {}
    available = models.get("availableModels") or models.get("available_models") or []
    ids: list[str] = []

    for entry in available:
        if isinstance(entry, dict):
            model_id = str(entry.get("modelId") or entry.get("model_id") or "").strip()
        else:
            model_id = str(entry).strip()
        if model_id:
            ids.append(model_id)

    return tuple(dict.fromkeys(ids))


def probe_acp_session(
    command: str,
    args: list[str] | tuple[str, ...] = (),
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_cache: bool = True,
) -> AcpSessionInfo:
    """Run `initialize` + `session/new` against a local ACP CLI and report.

    Never raises: a CLI that is missing, hung, or speaks something we cannot parse
    comes back as `installed=False` / `logged_in=False` with the reason in `detail`.
    A probe that throws would take the settings screen down with it, and the screen
    has to stay able to say "not configured yet".
    """
    argv = [str(a) for a in (args or [])]
    resolved = resolve_command(command)
    cache_key = f"{resolved or command} {' '.join(argv)}"

    if use_cache:
        _load_disk_cache()
        with _CACHE_LOCK:
            hit = _CACHE.get(cache_key)
            if hit and (time.time() - hit[0]) < CACHE_TTL_SECONDS:
                return hit[1]

    if not resolved:
        info = AcpSessionInfo(
            installed=False,
            logged_in=False,
            detail=f"{command or 'ACP CLI'} 不在这台机器上（Plobi 不代下载）",
            probed_at=time.time(),
        )
        with _CACHE_LOCK:
            _CACHE[cache_key] = (info.probed_at, info)
        return info

    proc = None
    lines: queue.Queue = queue.Queue()

    try:
        proc = subprocess.Popen(
            [resolved, *argv],
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
        _send(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
                "clientInfo": {"name": "plobi-probe", "version": "1"},
            },
        )
        init = _await_reply(lines, 1, deadline)
        if init is None:
            return _remember(cache_key, AcpSessionInfo(False, False, detail="ACP initialize 没有回应", probed_at=time.time()))
        if init.get("error"):
            message = str((init["error"] or {}).get("message") or "")
            return _remember(
                cache_key,
                AcpSessionInfo(True, False, detail=f"initialize 被拒：{message[:160]}"),
            )

        _send(proc, 2, "session/new", {"cwd": tempfile.gettempdir(), "mcpServers": []})
        session = _await_reply(lines, 2, deadline)
        if session is None:
            return _remember(
                cache_key,
                AcpSessionInfo(True, False, detail="session/new 没有回应", probed_at=time.time()),
            )
        if session.get("error"):
            err = session["error"] or {}
            message = str(err.get("message") or err)
            # An auth refusal is the interesting answer: the CLI is installed and
            # speaking ACP, the user just has not signed into it yet. Either way
            # `logged_in` is False — the two cases differ only in what we can say
            # about it, and that goes in `detail` (log / tooltip), not on the screen.
            return _remember(
                cache_key,
                AcpSessionInfo(
                    installed=True,
                    logged_in=False,
                    detail=message[:200] if _looks_like_auth_refusal(message) else f"session/new 被拒：{message[:160]}",
                    probed_at=time.time(),
                ),
            )

        result = session.get("result") or {}
        models = _model_ids(result)
        current = str(result.get("currentModelId") or result.get("current_model_id") or "").strip()
        if not models:
            # Signed in but silent about models: we do not fall back to a hand-copied
            # list, because that is exactly how wrong names get into the picker.
            return _remember(
                cache_key,
                AcpSessionInfo(True, True, detail="这条 CLI 没报回它自己的模型清单", probed_at=time.time()),
            )
        return _remember(
            cache_key,
            AcpSessionInfo(True, True, models=models, current_model=current, probed_at=time.time()),
        )
    except (OSError, ValueError, AssertionError) as exc:
        return _remember(
            cache_key,
            AcpSessionInfo(False, False, detail=f"{type(exc).__name__}: {exc}"[:200], probed_at=time.time()),
        )
    finally:
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass


def _remember(key: str, info: AcpSessionInfo) -> AcpSessionInfo:
    with _CACHE_LOCK:
        _CACHE[key] = (info.probed_at or time.time(), info)
    _write_disk_cache()
    return info


def cached_state(command: str, args: list[str] | tuple[str, ...] = ()) -> AcpSessionInfo | None:
    """A previous answer, if we have one that is still trustworthy. Never probes."""
    argv = [str(a) for a in (args or [])]
    key = f"{resolve_command(command) or command} {' '.join(argv)}"
    _load_disk_cache()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]
    return None


def invalidate(command: str, args: list[str] | tuple[str, ...] = ()) -> None:
    """Drop a cached answer — call this right after the user signs in."""
    argv = [str(a) for a in (args or [])]
    key = f"{resolve_command(command) or command} {' '.join(argv)}"
    with _CACHE_LOCK:
        _CACHE.pop(key, None)

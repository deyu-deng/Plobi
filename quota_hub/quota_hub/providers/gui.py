"""GUI-automation provider base — the GUI analog of CliProvider.

Some desktop apps (Tencent Marvis, the Workbuddy desktop client, ...) expose
NO CLI and NO inbound API. The compliant-ish way to use their quota from quota_hub
is to drive their chat UI the way a person would:

    locate the window -> type the prompt into the chat box ->
    press Enter       -> poll the response area until it settles ->
    read the latest reply

This base implements that loop GENERICALLY over Windows UI Automation (the
`uiautomation` package). Every app-specific detail — window title, the chat
input control, the response control, the submit key, timeouts — comes from
config, so a concrete provider only has to set `name` / `display_name` and
document its quirks. No token scraping, no reverse-engineering.

================================================================================
CAVEATS — read before enabling ANY GUI provider (deliberately, not hidden):
--------------------------------------------------------------------------------
  * FRAGILE      Depends on the app's exact control layout, which changes between
                versions. Run `quota_hub marvis-dump` / `quota_hub workbuddy-dump` on the
                target Windows machine to discover the real `input` / `output`
                selectors, then paste them into config.
  * PLATFORM     Windows UI Automation only (uiautomation lib). macOS / Linux /
                CI cannot run it — the provider disables itself gracefully
                (0 accounts) when the lib or the window is absent.
  * LIVE GUI     Cannot run headless. The target app must be installed, logged
                in, and running with its window visible on the gateway machine.
  * ToS GRAY     Automating a GUI to extract model output may breach the product's
                terms. Personal / local research only — do NOT ship this as a
                feature that defeats someone's paywall.
================================================================================
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from .base import Account, Capabilities, Credential, Provider, UpstreamError


def _as_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _chunk(cid: str, model: str, text=None, finish=None) -> dict:
    delta = {} if text is None else {"content": text}
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class GuiProvider(Provider):
    """Drive a desktop chat app through Windows UI Automation.

    Subclasses set `name` (registry key) and `display_name` (human label). All
    behavior is config-driven; nothing is hard-coded to a specific app version.
    """

    name = "gui"
    display_name = "GUI"
    default_capabilities = Capabilities(
        stream=True,
        tools=False,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="gray",
    )

    def __init__(self, config, http):
        super().__init__(config, http)
        if config.get("models"):
            self.served_models = tuple(config["models"])
        # lazily-imported uiautomation module (None = not installed, False = failed)
        self._ui_mod = None

    # --- lazy import of the Windows-only lib ------------------------------
    def _ui(self):
        if self._ui_mod is not None:
            return self._ui_mod or None
        try:
            import uiautomation as auto

            self._ui_mod = auto
        except Exception:
            self._ui_mod = False
        return self._ui_mod or None

    # --- discovery -------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        ui = self._ui()
        if ui is None:
            logging.getLogger("quota_hub").warning(
                "[%s] `uiautomation` not installed — GUI automation disabled. "
                "Install on a Windows GUI machine: pip install -r requirements.gui.txt",
                self.name,
            )
            self.accounts = []
            return []
        win = self._window(ui)
        if win is None:
            logging.getLogger("quota_hub").warning(
                "[%s] %s window not found (is it running & logged in?) — "
                "provider disabled until the app is up.",
                self.name,
                self.display_name,
            )
            self.accounts = []
            return []
        acc_id = (
            (self.config.get("accounts") or [{}])[0].get("id", f"{self.name}-default")
            if self.config.get("accounts")
            else f"{self.name}-default"
        )
        self.accounts = [
            Account(
                id=acc_id,
                provider=self.name,
                label=f"{self.display_name} (GUI)",
                cred=Credential(access_token="gui"),  # auth is the live GUI session
            )
        ]
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        ui = self._ui()
        if ui is None or self._window(ui) is None:
            raise UpstreamError(
                503,
                f"{self.name}: {self.display_name} window not available",
                retryable=True,
                cooldown=10.0,
            )

    # --- control location ------------------------------------------------
    def _window(self, ui):
        title = self.config.get("window_title", self.display_name)
        win = ui.WindowControl(RegexName=title, searchDepth=1)
        if win.Exists():
            return win
        win = ui.WindowControl(Name=title, searchDepth=1)
        return win if win.Exists() else None

    def _control(self, window, spec: dict):
        if not spec:
            return None
        ui = self._ui()
        ctype = spec.get("control", "Control")
        cls = getattr(ui, ctype, None)
        if cls is None:
            return None
        kwargs = {}
        if spec.get("regex_name"):
            kwargs["RegexName"] = spec["regex_name"]
        elif spec.get("name"):
            kwargs["Name"] = spec["name"]
        if spec.get("automation_id"):
            kwargs["AutomationId"] = spec["automation_id"]
        if spec.get("class_name"):
            kwargs["ClassName"] = spec["class_name"]
        if spec.get("depth") is not None:
            kwargs["searchDepth"] = spec["depth"]
        try:
            ctl = getattr(window, ctype)(**kwargs)
        except Exception:
            return None
        return ctl if ctl.Exists() else None

    @staticmethod
    def _read(control) -> str:
        for getter in ("Name",):
            try:
                v = getattr(control, getter, "")
                if v:
                    return _strip_ansi(str(v))
            except Exception:
                pass
        try:
            vp = control.GetValuePattern()
            if vp:
                v = vp.Value
                if v:
                    return _strip_ansi(str(v))
        except Exception:
            pass
        return ""

    def format_prompt(self, oai_req: dict) -> str:
        """Flatten the OpenAI conversation into one prompt (same shape as CliProvider)."""
        parts = []
        for m in oai_req.get("messages", []):
            role = m.get("role")
            text = _as_text(m.get("content"))
            if not text:
                continue
            if role == "system":
                parts.append(f"[system]\n{text}")
            elif role == "user":
                parts.append(f"[user]\n{text}")
            elif role == "assistant":
                parts.append(f"[assistant]\n{text}")
            else:
                parts.append(text)
        return "\n\n".join(parts)

    # --- the synchronous, blocking UI driver (runs in a worker thread) ----
    def _run_ui(self, acc: Account, oai_req: dict) -> str:
        ui = self._ui()
        if ui is None:
            raise RuntimeError("uiautomation is not available")
        win = self._window(ui)
        if win is None:
            raise RuntimeError(f"{self.display_name} window not found (app not running?)")
        inp = self._control(win, self.config.get("input", {}))
        if inp is None:
            raise RuntimeError("chat input control not found — fix `input` selector")
        out = self._control(win, self.config.get("output", {}))
        if out is None:
            raise RuntimeError("response control not found — fix `output` selector")

        prompt = self.format_prompt(oai_req)
        self._type(inp, prompt)
        return self._wait_reply(out)

    def _type(self, control, text: str) -> None:
        control.SetFocus()
        try:
            control.SetValue(text)
        except Exception:
            control.SendKeys(text)
        submit = self.config.get("send_key", "{Enter}")
        if submit:
            control.SendKeys(submit)

    def _wait_reply(self, out) -> str:
        timeout = float(self.config.get("response_timeout", 120))
        poll = float(self.config.get("stable_poll", 1.0))
        threshold = int(self.config.get("stable_threshold", 3))
        baseline = self._read(out)
        last = baseline
        stable = 0
        waited = 0.0
        while waited < timeout:
            time.sleep(poll)
            waited += poll
            cur = self._read(out)
            if cur != last:
                last = cur
                stable = 0
            else:
                stable += 1
                if stable >= threshold:
                    break
        final = self._read(out)
        if baseline and final.startswith(baseline):
            return final[len(baseline) :].strip()
        return final.strip()

    # --- request path (async wrappers around the blocking driver) --------
    def parse_response(self, text: str, oai_req: dict) -> dict:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": oai_req["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        try:
            text = await asyncio.to_thread(self._run_ui, acc, oai_req)
        except RuntimeError as e:
            raise UpstreamError(503, f"{self.name}: {e}")
        if not text:
            raise UpstreamError(502, f"{self.name}: empty reply from GUI")
        return self.parse_response(text, oai_req)

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        # GUI apps can't stream tokens; we read the settled reply and emit it as
        # one content chunk + a stop chunk (valid SSE, just not incremental).
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        model = oai_req["model"]
        try:
            text = await asyncio.to_thread(self._run_ui, acc, oai_req)
        except RuntimeError as e:
            raise UpstreamError(503, f"{self.name}: {e}")
        if text:
            yield _chunk(cid, model, text)
        yield _chunk(cid, model, None, finish="stop")

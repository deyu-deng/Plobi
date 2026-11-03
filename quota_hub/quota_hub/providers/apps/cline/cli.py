"""Cline — its own OpenAI-compatible door, the only door.

Measured on this machine 2026-10-31: `api.cline.bot/api/v1` is a real OpenAI-shaped
door for this account (439 ids, `finish_reason="tool_calls"` back in 0.7-7.1 s), so
`http.py` serves the turn whenever a credential exists. There is no ACP spawn fallback:
without a credential the leg raises `UpstreamError(reauth=True)` and the card asks the
owner to run Cline's own login. Login remains Cline's own command, in a window: `cline
auth` prints "interactive auth setup requires a TTY" when its stdout is a pipe
(re-measured 2026-10-28), so the card opens a console instead of a pipe and keeps the
exit code.
"""
from __future__ import annotations

from ...base import Capabilities, Provider
from .http import ClineHttpMixin


class ClineCliProvider(ClineHttpMixin, Provider):
    name = "cline_cli"
    display_name = "Cline"
    binary = "cline"
    #: 模型 id 的前缀**必须**等于这张卡在名册里的 id。桌面那条链是死的：
    #: `electron/main.ts` 按 `/v1/models` 每行的 `provider`（=卡 id）剥一次前缀，
    #: `src/store/desktop-quotas.ts` 再把卡片 id 拼回去当选择值——两边都用同一个名字
    #: 才对得上。10-31 现场：这里写 `cline` 而卡叫 `cline_cli`，于是选择值成了
    #: `cline_cli/cline/moonshotai/…` 这种三段名，切换当场报「Model switch failed」。
    #: 所以这里**不写** `model_prefix`：`_prefix()` 拿 `self.name` 兜底。
    login_args = ("auth",)
    login_tty = True

    def login_plan(self) -> dict:
        """Cline's own auth command, in a window (it refuses a pipe: see module doc)."""
        return {
            "kind": "cli_login",
            "binary": self.binary,
            "argv": list(self.login_args),
            "timeout": 300.0,
            "tty": True,
            "name": self.display_name or self.name,
        }

    def capabilities(self) -> Capabilities:
        caps = self.default_capabilities.as_dict()
        caps["tools"] = True
        return Capabilities(**caps)

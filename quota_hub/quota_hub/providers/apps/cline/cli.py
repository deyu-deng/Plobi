"""Cline — its own OpenAI-compatible door first, the ACP session as the floor.

Measured on this machine 2026-10-29 with cline 3.0.74 (`cline --acp`, JSON-RPC over
stdio): `initialize` answers with `agentInfo` and three `authMethods`, and `session/new`
refuses with `-32000 Authentication required: Call authenticate before starting a
session` until he has signed in. Once it accepts, the same answer carries the models that
session can run. That session runs *its* tools and returns a finished answer, so a tool
call has nowhere to go in our loop.

Re-measured 2026-10-31: `api.cline.bot/api/v1` is a real OpenAI-shaped door for this
account (439 ids, `finish_reason="tool_calls"` back in 0.7-7.1 s), so `http.py` serves the
turn whenever a credential exists; `providers/acp.py` stays behind it for the case where
there is none. Login remains Cline's own command, in a window: `cline auth` prints
"interactive auth setup requires a TTY" when its stdout is a pipe (re-measured
2026-10-28), so the card opens a console instead of a pipe and keeps the exit code.
"""
from __future__ import annotations

from ...acp import AcpProvider
from .http import ClineHttpMixin


class ClineCliProvider(ClineHttpMixin, AcpProvider):
    name = "cline_cli"
    display_name = "Cline"
    binary = "cline"
    acp_args = ("--acp",)
    #: 模型 id 的前缀**必须**等于这张卡在名册里的 id。桌面那条链是死的：
    #: `electron/main.ts` 按 `/v1/models` 每行的 `provider`（=卡 id）剥一次前缀，
    #: `src/store/desktop-quotas.ts` 再把卡片 id 拼回去当选择值——两边都用同一个名字
    #: 才对得上。10-31 现场：这里写 `cline` 而卡叫 `cline_cli`，于是选择值成了
    #: `cline_cli/cline/moonshotai/…` 这种三段名，切换当场报「Model switch failed」。
    #: 所以这里**不写** `model_prefix`：`providers/acp.py` 拿 `self.name` 兜底。
    login_args = ("auth",)
    login_tty = True

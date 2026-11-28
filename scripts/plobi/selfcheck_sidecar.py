"""装出来的侧车能不能启动，一条命令问清楚（发布硬闸）。

0.3.4 的事故不是配置写错，也不是依赖没装：包打得出来、装得上、55 条单测全绿，用户
一点引擎就 ``password_env names '...' but that variable is not set``。根因是侧车
（计划任务拉起、提权、**没有登录 shell 的环境继承**）从没读过根 ``.env``——而开发机
上它永远读得到，因为开发者是从装好依赖的终端里跑的。

所以「装完能不能用」只能由**装出来的那份代码**在**它自己的进程里**自证。本脚本造一个
最小的 PLOBI_HOME（一份能编译的 egress 表 + 一份只有凭据的 .env），把侧车的
``--self-check`` 当黑盒跑两遍：

1. 正常夹具 → 必须 PASS；
2. 把凭据从 .env 里抽走 → 必须 FAIL。

第 2 条是为了证明这道闸本身没有恒绿：一条永远返回 0 的检查比没有检查更危险。

用法（CI 里由 build 作业在离线 venv 上跑，本地直接跑也行）：

    PLOBI_SELFCHECK_BACKEND=<backend 源码树> python scripts/plobi/selfcheck_sidecar.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

#: 夹具里的凭据变量名与假值。刻意不叫真实的名字：这个变量只该出现在临时 home 里。
PASSWORD_ENV = "PLOBI_SELFCHECK_PASSWORD"
PASSWORD = "selfcheck-not-a-real-secret"

CONFIG = textwrap.dedent(
    f"""\
    network:
      egress:
        engine:
          enabled: true
          mixed_port: 7890
          tun:
            enabled: false
        nodes:
          probe:
            type: ss
            server: 127.0.0.1
            port: 8388
            cipher: aes-256-gcm
            udp: true
            password_env: {PASSWORD_ENV}
        rules:
          - match: '*'
            via: proxy:probe
    """
)

#: 侧车是「系统变量之外什么都没有」的环境里出生的；探针必须复刻这一点，
#: 否则测的是开发者的终端，不是装机版。
_KEEP = ("SystemRoot", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "HOME", "LANG", "LC_ALL")
_TIMEOUT_S = 300


def _backend_root() -> Path:
    """源码树根：``agent`` 包所在的那一层。CI 里指向打包后的 backend。"""
    override = os.environ.get("PLOBI_SELFCHECK_BACKEND")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def _write_home(root: Path, *, with_credential: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(CONFIG, encoding="utf-8")
    body = f"{PASSWORD_ENV}={PASSWORD}\n" if with_credential else "PLOBI_SELFCHECK_UNRELATED=1\n"
    (root / ".env").write_text(body, encoding="utf-8")
    return root


def _run(home: Path) -> tuple[int, str]:
    backend = _backend_root()
    env = {k: os.environ[k] for k in _KEEP if k in os.environ}
    env["PLOBI_HOME"] = str(home)
    env["PYTHONPATH"] = str(backend)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "agent.egress.helper", "--self-check", "--home", str(home)],
        cwd=str(backend),
        env=env,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="plobi-selfcheck-") as tmp:
        base = Path(tmp)

        good = _write_home(base / "good", with_credential=True)
        rc, out = _run(good)
        print(f"[gate] 夹具齐全 → rc={rc}")
        print(out)
        if rc != 0:
            failures.append(
                "侧车在自己的进程里起不来：装机版会一模一样地坏在用户机器上（见上）"
            )

        bad = _write_home(base / "bad", with_credential=False)
        rc2, out2 = _run(bad)
        print(f"[gate] 抽走凭据 → rc={rc2}")
        print(out2)
        if rc2 == 0:
            failures.append(
                "抽走凭据后仍然 PASS：这道闸是恒绿的，等于没有——先修检查本身"
            )

    if failures:
        for item in failures:
            print(f"::error::{item}", file=sys.stderr)
        print("SELFCHECK GATE: FAILED")
        return 1
    print("SELFCHECK GATE: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

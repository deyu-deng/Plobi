"""新刀 WP-OFFLOAD-RUN-DEFECTS 的判据（四条缺陷各一个钉子）。

钉的是**机制**，不是这台机器的现状：测试必须在那台没装 cline/gemini 的机器上也绿。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as offload  # noqa: E402
from tools import offload_tools as tools_offload  # noqa: E402


# ── 缺陷①：Windows 命令行按 ASCII 空白切词 ⇒ 提示词正文必须走文件 ────────────

def test_a_prompt_without_ascii_whitespace_must_go_through_a_file():
    """中文提示词里**没有 ASCII 空白**（中文用全角冒号与逗号）。

    Python 的 `subprocess.list2cmdline` 只在参数含 ASCII 空格/制表符时才加引号
    ⇒ 一段纯中文**一个引号都不加** ⇒ 整段提示词在 Windows 命令行上被切成
    多个 argv 元素 ⇒ cline 把第一段当命令名（回 `Unknown command or unquoted prompt`）。

    ⇒ 这样的提示词**必须**写成文件，命令行上只留一句 ASCII 指令。
    """
    chinese = "这一趟的任务书在下面这个文件里，它是唯一的任务来源：C:/x/y.md。先读它。"
    assert not any(ch in chinese for ch in " \t"), "这一句故意不含 ASCII 空白"
    assert offload.needs_task_file("cline.cmd", chinese), "中文提示词必须走文件"

    ascii_prompt = "Read C:/x/y.md and follow it exactly."
    assert offload.needs_task_file("cline.cmd", ascii_prompt) is False, \
        "含 ASCII 空白的提示词 argv 不会被切开，原样内联即可"


def test_the_file_instruction_is_ascii_so_the_shell_cannot_split_it():
    """那一句指令是**给厂商看的话**：必须是 ASCII 且含 ASCII 空格
    ⇒ Python 一定会给它加引号 ⇒ argv 元素不会被切开。"""
    prompt = offload.task_file_prompt(Path("C:/x/y.md"))
    assert prompt.isascii(), "指令必须是 ASCII（中文正文在文件里，不在命令行上）"
    assert " " in prompt, "必须含 ASCII 空格 ⇒ list2cmdline 一定会加引号"


def test_the_npm_shim_is_bypassed_so_no_cmd_shell_eats_the_quotes():
    """垫片 → 真实 Node 入口：解析垫片最后一行那固定形状
    （`"%_prog%" "%dp0%\\node_modules\\...\\<entry>" %*`），
    ⇒ 直接起 `node.exe <entry>`，**中间不经过 cmd.exe**。"""
    shim = Path("D:/fake/cli/pkg.cmd")
    shim.parent.mkdir(parents=True, exist_ok=True)
    entry = shim.parent / "node_modules" / "pkg" / "bin" / "entry.js"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("// fake\n", encoding="utf-8")
    shim.write_text(
        '@echo off\r\n'
        'IF EXIST "%dp0%\\node.exe" (\r\n'
        '  SET "_prog=%dp0%\\node.exe"\r\n'
        ') ELSE (\r\n'
        '  SET "_prog=node"\r\n'
        ')\r\n'
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & '
        '"%_prog%" "%dp0%\\node_modules\\pkg\\bin\\entry.js" %*\r\n',
        encoding="utf-8",
    )
    try:
        found = offload.resolve_node_entry(str(shim))
        if found is None:
            pytest.skip("这台机器上没有 node.exe（垫片解析要靠它）")
        node, script = found
        assert script.lower().endswith(os.path.join("pkg", "bin", "entry.js"))
        assert os.path.basename(node).lower() == "node.exe"

        argv = offload.spawn_argv([str(shim), "--json", "-y", "prompt"])
        if os.name == "nt":
            assert argv[0].lower().endswith("node.exe"), "绕开 cmd.exe"
            assert argv[1].lower().endswith("entry.js")
            assert "cmd.exe" not in [a.lower() for a in argv]
            assert argv[2:] == ["--json", "-y", "prompt"], "参数一个不许少、也不许换位置"
    finally:
        import shutil
        shutil.rmtree(str(shim.parent), ignore_errors=True)


def test_a_shim_we_cannot_parse_still_falls_back_to_cmd():
    """解析不出来**照旧走 `cmd /c`**——不许因为绕行失败就不派活。"""
    bogus = Path("D:/fake/cli/bogus.cmd")
    bogus.parent.mkdir(parents=True, exist_ok=True)
    bogus.write_text("@echo off\r\necho hi\r\n", encoding="utf-8")
    try:
        assert offload.resolve_node_entry(str(bogus)) is None
        if os.name == "nt":
            argv = offload.spawn_argv([str(bogus), "--x"])
            assert argv[0].lower().endswith(("cmd.exe", "comspec"))
    finally:
        import shutil
        shutil.rmtree(str(bogus.parent), ignore_errors=True)


# ── 缺陷②：登录态照实判（过期 token 不算登上了） ──────────────────────────

def test_an_expired_credential_file_is_not_logged_in(tmp_path, monkeypatch):
    """**文件在 ≠ 登上了**（11-08 21:5x 实测：agy 的令牌文件在，但 OAuth 过期）。

    光看文件在不在，会把一个已经过期的目标判成「能用」——**屏上的假话**。
    """
    cred = tmp_path / "token.json"
    cred.write_text(
        '{"token": {"access_token": "x", "expiry": "2020-01-01T00:00:00.000000Z"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(tools_offload, "_LOGIN_EVIDENCE", {
        "expiredone": (f"json-expiry:{cred}#token.expiry",),
    })
    assert tools_offload._probe_logged_in("expiredone") is False, \
        "过期的凭据不能算登上了"

    fresh = tmp_path / "fresh.json"
    fresh.write_text(
        '{"token": {"access_token": "x", "expiry": "2999-01-01T00:00:00.000000Z"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(tools_offload, "_LOGIN_EVIDENCE", {
        "freshone": (f"json-expiry:{fresh}#token.expiry",),
    })
    assert tools_offload._probe_logged_in("freshone") is True


def test_the_expiry_field_reader_never_returns_the_token_itself():
    """只读**那一个**过期字段：不读、不返回、不打印任何 token 值。"""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.json"
        path.write_text(
            json.dumps({"token": {"access_token": "SECRET", "expiry": "2020-01-01T00:00:00Z"}}),
            encoding="utf-8",
        )
        verdict = tools_offload._credential_expiry(path, "token.expiry")
        assert verdict is False
        assert "SECRET" not in str(verdict)


def test_gemini_is_judged_by_its_own_auth_not_by_another_products_token():
    """gemini 的登录落点：`settings.json` 的 auth 段 + 三个环境变量。

    **不许**把 `oauth_creds.json` 当 gemini 的证据——那是**另一个产品**
    （Antigravity）的凭据，Gemini CLI 自己不认它（11-08 真跑回
    `Please set an Auth method in your ...settings.json`）。
    """
    hints = tools_offload._LOGIN_EVIDENCE["gemini"]
    assert not any("oauth_creds" in h for h in hints), \
        "oauth_creds.json 是 Antigravity 的凭据，对 gemini 不是证据"
    assert any("settings.json" in h for h in hints)
    for var in ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA"):
        assert f"env:{var}" in hints, f"{var} 是官方给的另一条认证途径"


def test_agy_is_judged_by_its_token_expiry_not_by_the_file_merely_existing():
    """agy 的证据要带那一个过期字段（它的 OAuth 会过期）。"""
    hints = tools_offload._LOGIN_EVIDENCE["agy"]
    assert any(h.startswith("json-expiry:") for h in hints), \
        "光看文件在不在会把过期的 agy 判成「能用」"
    assert any("antigravity-oauth-token" in h for h in hints)


# ── 缺陷③：gemini 加 --skip-trust ──────────────────────────────────────────

def test_gemini_is_told_to_trust_the_workspace():
    """`gemini --help` 现核有 `--skip-trust`（Trust the current workspace for this session）。

    不加它，gemini 在非受信目录里会回
    `Approval mode overridden to "default" because the current folder is not trusted`
    ⇒ 我们写的放开档位被它自己关回去。
    """
    template = offload.agent_spec("gemini").argv_template
    assert "--skip-trust" in template
    # **只有 gemini 加**：别的家没这枚旗标，加了就是非法参数。
    for name in ("cline", "opencode", "codex", "claude", "qoder", "kiro", "qwen",
                 "copilot", "kilo", "codebuddy", "agy"):
        assert "--skip-trust" not in offload.agent_spec(name).argv_template, \
            f"{name} 没有这枚旗标，别人的旗标不许抄过来"


def test_geminis_prompt_is_only_ever_one_argument():
    """gemini 回 `Cannot use both a positional prompt and the --prompt (-p) flag together`
    ⇒ `-p` 后面只许跟提示词本身，不许再当位置参数补一遍。"""
    template = offload.agent_spec("gemini").argv_template
    assert template.count("{task}") == 1
    assert template.index("-p") < template.index("{task}"), "-p 的值必须紧跟其后"


# ── 缺陷④：超时杀进程树 ────────────────────────────────────────────────────

def test_a_timeout_kills_the_whole_tree_not_just_the_shell(tmp_path):
    """`subprocess.run(timeout=)` 只杀直接那个孩子；孙进程攥着管道继续跑
    ⇒ 11-08 21:2x 实测 qoder 报 180s 超时、实际跑了 499s。

    这一格钉的是**真会杀掉孙进程**：起一个会自己生孙进程的短命脚本，
    给它一个很短的时限，超时后确认**孙进程也不在了**。
    """
    marker = tmp_path / "grandchild.pid"
    script = tmp_path / "spawner.py"
    script.write_text(
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        offload._run_child(
            [sys.executable, str(script), str(marker)],
            cwd=str(tmp_path), env=dict(os.environ), timeout=3,
        )
    # 超时那一支必须带 process 属性（调用方要靠它如实读出「杀掉了没有」）。
    assert getattr(caught.value, "process", None) is not None
    note = offload._kill_descendants(caught.value.process)
    assert note, "杀进程树必须给一句人能读的读数"

    # 孙进程真的没了（给它一点时间退出）。
    import time
    deadline = time.time() + 20
    while time.time() < deadline:
        if marker.exists():
            pid = int(marker.read_text(encoding="utf-8").strip())
            alive = True
            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                alive = False
            if not alive:
                break
        time.sleep(0.5)
    else:
        pytest.fail("孙进程还活着 ⇒ 进程树没杀干净")

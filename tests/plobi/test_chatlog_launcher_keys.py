"""启动器那一格的卫生判据：密钥不进命令行、引擎输出不落 stdout、配置目录钉在 D 盘家。

这台 10-17 17:5x 的现核是这些断言的起因，不是假想：
`scripts/chatlog_server.ps1` 把 `--data-key` 拼进 argv，于是任何进程一句
`Get-CimInstance Win32_Process` 就能把 32 字节读走；同一支脚本还把整条命令打在 stdout。
而 vendor 那侧 `internal/chatlog/conf/conf.go:71-73` 的兜底条件是
`DataDir != "" && DataKey == ""` —— argv 给了 key 就**永不**读那份带 `platform/version`
的配置，于是 `/api/v1/session` 稳定 503「unsupported platform:  v0」。
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "chatlog_server.ps1"
VENDOR_CONF = Path(__file__).resolve().parents[2] / "tools" / "chatlog" / "internal" / "chatlog" / "conf" / "conf.go"


def _source() -> str:
    assert SCRIPT.is_file(), f"启动器不在预期位置：{SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


def test_secrets_never_reach_the_argument_list() -> None:
    source = _source()
    # 只看真正拼 argv 的那几行（注释里在解释为什么不能用，所以不许拿整份文本做子串判断）。
    argv_lines = [
        line.strip()
        for line in source.splitlines()
        if "$argsList" in line and not line.strip().startswith("#")
    ]
    assert argv_lines, "找不到命令行构造段"
    allowed = ("server", "--addr", "--debug")
    for line in argv_lines:
        assert not re.search(r"--data-key|--img-key|--data-dir|--auto-decrypt", line), (
            f"密钥或档位被拼进命令行（任何进程可读 argv，且会让 vendor 不再读配置里的 platform）：{line}"
        )
        assert any(token in line for token in allowed), f"argv 构造行只许这三样，读出：{line}"


def test_the_command_line_is_not_echoed() -> None:
    # 一次性把整条命令打进 stdout＝把 argv 抄进日志；这一行曾经就长这样。
    assert not re.search(r'Write-Host.*\$\(argsList -join', _source())


def test_engine_output_is_discarded_not_logged() -> None:
    source = _source()
    # conf.go:93 把合并后的整包服务配置（含 data_key/img_key）打进引擎日志 ⇒ 启动器必须丢输出。
    assert re.search(r"& \$BinaryPath @argsList.*\|\s*Out-Null", source), "引擎 stdout/stderr 必须整体丢弃"
    # 而 `$ErrorActionPreference = "Stop"` 下直接 `2>&1` 会让原生 stderr 当场抛出（本机现核退出码 1）。
    launch = source.split("# ---- Launch")[1]
    assert 'ErrorActionPreference = "Continue"' in launch, "丢弃 stderr 那一档必须先把 EAP 降下来"


def test_config_dir_is_pinned_and_named_as_the_engine_expects() -> None:
    source = _source()
    assert VENDOR_CONF.is_file(), f"vendor 配置源码不在预期位置：{VENDOR_CONF}"
    vendor = VENDOR_CONF.read_text(encoding="utf-8")
    config_name = re.search(r'ServerConfigName\s*=\s*"([^"]+)"', vendor).group(1)
    assert f'Join-Path $configDir "{config_name}.json"' in source, (
        f"启动器写的文件名要和 vendor 的 ServerConfigName（{config_name}）一致"
    )
    assert re.search(r'\$env:CHATLOG_DIR\s*=\s*\$configDir', source), (
        "不钉 CHATLOG_DIR ⇒ pkg/config/config.go:60-65 回退 os.UserHomeDir()\\.chatlog（C 盘，裁定 109 三）"
    )
    assert "UTF8Encoding($false)" in source, "带 BOM 的 JSON 引擎读不出"


def test_platform_is_the_vendors_canonical_word_not_the_handoff_shorthand() -> None:
    source = _source()
    # datasource.go:41-43 拿 platform 逐字比 `windows`；handoff 里那份写的是简称 `win`。
    assert re.search(r'"win"\s*\{?\s*\$Platform\s*=\s*"windows"', source), "档位名要换成引擎那侧的写法"

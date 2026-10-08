"""跨语言对照（裁定 94 第 1 条）：Python 侧这一半表。

TS 那一半在 `apps/desktop/electron/aigw-key-reconcile.test.ts`，同一批用例、同一批期望值。
两个解析器读同一个文件（`aigw/config.yaml` 的 `server.api_key`），只要有一侧改了语义而
没同步这张表，就会有一侧红 —— 这是裁定 94 明确接受的成本，不是税。

值全部现测于 `gateway_declared_api_key()`（2026-10-08，本机），不是从注释抄的。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from plobi.agents.registry import AIGW_DEV_DEFAULT_API_KEY, gateway_declared_api_key

# (name, api_key 标量, 环境, 期望解析值)
PARITY = [
    ("placeholder-caps", "${AIGW_KEY:-sk-from-placeholder}", {}, "sk-from-placeholder"),
    ("placeholder-lower", "${aigw_key:-X}", {}, "${aigw_key:-X}"),
    ("brace-in-default", "${AIGW_KEY:-a}b}", {}, "${AIGW_KEY:-a}b}"),
    ("double-quoted", "''k''", {}, "k"),
    ("empty-string", '""', {}, ""),
    ("plain", "sk-local-dev-key", {}, "sk-local-dev-key"),
    ("placeholder-env-set", "${AIGW_KEY:-fallback}", {"AIGW_KEY": "from-env"}, "from-env"),
    ("trailing-comment", "sk-local-dev-key  # rotated 2026-10-08", {}, "sk-local-dev-key"),
]

# 结构性形状：整份文件而不是一个标量
PARITY_WHOLE_FILE = [
    ("no-api-key-line", "server:\n  port: 8000\n", ""),
    ("no-server-block", "logging:\n  level: INFO\n", ""),
    ("empty-file", "", ""),
]


def _declared(scalar: str, env: dict[str, str], monkeypatch) -> str:
    for k in ("AIGW_KEY",):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    path = Path(tempfile.mkdtemp()) / "config.yaml"
    path.write_text(f"server:\n  host: 127.0.0.1\n  api_key: {scalar}\n", encoding="utf-8")
    return gateway_declared_api_key(path)


@pytest.mark.parametrize("name,scalar,env,expected", PARITY, ids=[r[0] for r in PARITY])
def test_parity_with_the_typescript_reader(name, scalar, env, expected, monkeypatch):
    assert _declared(scalar, env, monkeypatch) == expected, (
        f"{name}: Python 侧解析变了 —— TS 那张表要同时改，否则消费者对不上"
    )


@pytest.mark.parametrize("name,text,expected", PARITY_WHOLE_FILE, ids=[r[0] for r in PARITY_WHOLE_FILE])
def test_parity_whole_file_shapes(name, text, expected, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    assert gateway_declared_api_key(path) == expected


def test_missing_file_is_indistinguishable_from_declaring_nothing(tmp_path):
    """Python 侧：文件不存在与"没声明"都返回 `""`，调用方再退到共用默认值。

    TS 侧故意不同：它知道文件在不在，"没法看"要报成 NOT CHECKED。这条差异写在
    TS 测试里，别哪天被"统一"掉。
    """
    assert gateway_declared_api_key(tmp_path / "nope.yaml") == ""
    assert (AIGW_DEV_DEFAULT_API_KEY or "x") == "sk-local-dev-key"

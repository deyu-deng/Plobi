"""导出的包里**不许有凭据**（2026-11-06 补）。

旧形状那条导出（``export_profile``）会过滤 ``auth.json``／``.env``；家目录退休后 CLI
走的是 :func:`export_identity`（名册那一行 ＋ 它的身份文件）——而名册行上有一格
``secret_overrides``（裁定 42 §42.2 第 6 项：人设片段／模型／密钥，只有这三样允许
按 Agent 不同）。**那一格是密钥**：它不进导出包——导出的是**配置**，凭据走共享层
那份池子，不随配置走（`apply_secret_overrides` 只在运行时注入子进程环境）。

另外两档钉的是排除名单本身（``auth.json``／``.env`` 仍在其中）：名单留着，哪天
「导整份家」那条路要是回来，得继续用它。

跑法：``bash scripts/run_tests.sh tests/plobi_cli/test_agent_export_credentials.py``
"""

import tarfile
from pathlib import Path

import pytest

from plobi_cli.agent_identity import export_identity, _DEFAULT_EXPORT_EXCLUDE_ROOT

ROSTER_YAML = (
    "agents:\n"
    "  Aura:\n"
    "    role: l2_project\n"
    "    provider: minimax-cn\n"
    "    model: MiniMax-M2\n"
    "    secret_overrides:\n"
    "      MINIMAX_CN_API_KEY: sk-secret-value\n"
)


@pytest.fixture()
def roster_home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME ＋ 一份名册（名册行上带密钥覆盖那一格）。"""
    home = tmp_path / ".plobi"
    (home / "plobi").mkdir(parents=True)
    (home / "profiles").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    roster = home / "plobi" / "projects.yaml"
    roster.write_text(ROSTER_YAML, encoding="utf-8")
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    return home


class TestCredentialExclusion:

    def test_auth_json_in_default_exclude_set(self):
        """auth.json must be in the default export exclusion set."""
        assert "auth.json" in _DEFAULT_EXPORT_EXCLUDE_ROOT

    def test_dotenv_in_default_exclude_set(self):
        """.env must be in the default export exclusion set."""
        assert ".env" in _DEFAULT_EXPORT_EXCLUDE_ROOT

    def test_secret_overrides_never_reach_the_archive(self, roster_home, tmp_path):
        """名册行上的密钥覆盖不进导出包；配置那一半照旧在包里。"""
        output = tmp_path / "export" / "aura.tar.gz"
        output.parent.mkdir(parents=True, exist_ok=True)
        result = export_identity("Aura", str(output))

        with tarfile.open(result, "r:gz") as tf:
            names = tf.getnames()
            row_files = [n for n in names if n.endswith("agent.yaml")]
            assert row_files, "名册那一行必须在包里"
            raw = b"".join(tf.extractfile(n).read() for n in row_files)

        assert b"sk-secret-value" not in raw, "密钥值不许进导出包"
        assert b"secret_overrides" not in raw, "密钥那一格不许进导出包"
        assert b"MiniMax-M2" in raw, "模型覆盖不是凭据，照旧在包里"

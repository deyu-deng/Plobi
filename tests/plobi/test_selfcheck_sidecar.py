"""发布闸门的自我防护：``scripts/plobi/selfcheck_sidecar.py``。

闸门脚本自己腐化是最难发现的一种坏——CI 依旧绿，因为它绿得没有条件。所以这里两件事：

* 夹具配置必须被**真解析器**接受（夹具写错，闸门就变成误报源）；
* 整体跑一遍：齐全必须 PASS，抽走凭据必须 FAIL。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from agent.egress.rules import load_table

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "plobi" / "selfcheck_sidecar.py"


def _gate():
    spec = importlib.util.spec_from_file_location("selfcheck_sidecar", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_fixture_table_is_accepted_by_the_real_parser(tmp_path):
    gate = _gate()
    home = gate._write_home(tmp_path / "home", with_credential=True)
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))

    table = load_table(config=config)

    assert table.unreadable == ""
    assert "probe" in table.nodes
    assert table.nodes["probe"].secret_env == {"password": gate.PASSWORD_ENV}


def test_the_gate_passes_when_the_fixture_is_complete(tmp_path, monkeypatch):
    gate = _gate()
    monkeypatch.setenv("PLOBI_SELFCHECK_BACKEND", str(ROOT))

    rc, out = gate._run(gate._write_home(tmp_path / "good", with_credential=True))

    assert rc == 0, out
    assert "SELF-CHECK: ok" in out
    assert "[PASS] credentials" in out


def test_the_gate_fails_when_the_credential_is_absent(tmp_path, monkeypatch):
    gate = _gate()
    monkeypatch.setenv("PLOBI_SELFCHECK_BACKEND", str(ROOT))

    rc, out = gate._run(gate._write_home(tmp_path / "bad", with_credential=False))

    assert rc != 0, out
    assert "[FAIL] credentials" in out

"""WP-AIGW-KEY-LITERALS (`scripts/` grid): the start script reconciles its probe
key against the key the gateway declares, and goes red instead of guessing.

Background (待决 #29, ruled 2026-10-08): the gateway owns the value (甲), consumers
may keep a fallback but must reconcile at runtime and be *visible* when they
disagree (乙).  Before this change `aigw_start.ps1` probed `/v1/models` with a
Bearer literal written into the script, so a rotated gateway key showed up as an
unexplained 401 — the same misdiagnosis this workspace already paid for twice.

These tests pin the *relationship*, never a key value:

  * the script's answer must agree with `plobi/agents/registry.py`'s declaration
    reader, which is the one true source for "what does the gateway declare";
    the expected fingerprints here are derived from that reader, not copied;
  * agreement passes, disagreement goes red naming both sides, and anything that
    prevents us from reading the declaration is reported as NOT_CHECKED — which
    is explicitly *not* a pass;
  * no plaintext key ever reaches the console.

They drive the real PowerShell script through `powershell.exe -File`, but only in
`-KeyCheck` mode: that path reads a config file and asks the interpreter, it does
not open a socket, does not start the gateway, and does not send a chat request.
Every config it reads is a file in a pytest tmp dir, so the real
`aigw/config.yaml` never has to be edited to prove the contract.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PS1 = _REPO_ROOT / "scripts" / "plobi" / "aigw_start.ps1"

# Env vars that feed the key this script resolves.  Every run starts from a
# cleaned env so a developer's real credential cannot decide a verdict.
_KEY_ENV_VARS = ("AIGW_API_KEY", "PLOBI_QUOTA_AIGW_KEY", "AIGW_KEY")

# Test-only stand-ins.  Asserting that these never appear in the output is how
# the tests prove the script does not leak what it read.
FAKE_DECLARED = "sk-test-declared-aaaa"
FAKE_ROTATED = "sk-test-rotated-bbbb"
FAKE_STALE_ENV = "sk-test-stale-env-cccc"
FAKE_PLACEHOLDER_DEFAULT = "sk-test-placeholder-default-dddd"
PLACEHOLDER_ENV = "WP_AIGW_KEYLITERALS_VAR"

_PWSH = shutil.which("powershell.exe") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    not (_PWSH and sys.platform.startswith("win")),
    reason="aigw_start.ps1 is the Windows start script; no PowerShell host here",
)


def fp(value: str) -> str:
    """The fingerprint shape the script reports: SHA-256 hex, first 12."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def declared_by_the_shared_reader(config_path: Path) -> str:
    """What the gateway's own declaration reader says about this file.

    The script asks this same function (through `python -c`) instead of growing a
    PowerShell parser, so comparing the two closes the "two families drifting over
    how to read one file" hole this work package is about.
    """
    from plobi.agents.registry import gateway_declared_api_key

    return gateway_declared_api_key(config_path)


def write_config(directory: Path, name: str, text) -> Path:
    path = directory / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


def run_key_check(
    config_path: Path,
    *,
    env_extra: dict[str, str] | None = None,
    script_path: Path | None = None,
    strip_path: bool = False,
    drop_pathext: bool = False,
) -> subprocess.CompletedProcess:
    """Run the script's `-KeyCheck` path — reconcile only, no port, no start."""
    blocked = {name.upper() for name in _KEY_ENV_VARS} | {PLACEHOLDER_ENV}
    base = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in blocked
    }
    # The hermetic test environment (scripts/run_tests.sh hands over an opt-in
    # variable list) has no PATHEXT, and Windows PowerShell then refuses to start
    # *any* native child without saying so.  Supply the extension list — never a
    # path — so the script's own interpreter call can actually run.  Windows env
    # keys come back in arbitrary casing, so both lookups are case-insensitive.
    if not any(key.upper() == "PATHEXT" for key in base):
        base["PATHEXT"] = ".COM;.EXE;.CMD;.BAT"
    if drop_pathext:
        base = {key: value for key, value in base.items() if key.upper() != "PATHEXT"}
    if strip_path:
        # System32 only: no interpreter on PATH, and the script copy sits in a
        # tree with no .venv, so asking the gateway's reader must fail.
        system_root = next(
            (value for key, value in base.items() if key.upper() == "SYSTEMROOT"),
            None,
        )
        assert system_root, "expected SYSTEMROOT for the no-interpreter scenario"
        base["PATH"] = str(Path(system_root) / "System32")
    base.update(env_extra or {})
    cmd = [
        _PWSH,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path or _PS1),
        "-Config",
        str(config_path),
        "-KeyCheck",
    ]
    return subprocess.run(
        cmd,
        env=base,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def output_of(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


def test_matching_key_is_reconciled_and_passes(tmp_path: Path):
    """Script key == gateway declaration → exit 0, and the fingerprint the script
    reports is the one the shared reader produces for that config."""
    config = write_config(tmp_path, "match.yaml", f"server:\n  api_key: {FAKE_DECLARED}\n")
    result = run_key_check(config, env_extra={"AIGW_API_KEY": FAKE_DECLARED})

    out = output_of(result)
    assert result.returncode == 0, out
    assert "AIGW_KEY_VERDICT=MATCH" in out
    assert f"gateway_fp={fp(FAKE_DECLARED)}" in out
    assert f"script_fp={fp(FAKE_DECLARED)}" in out
    assert declared_by_the_shared_reader(config) == FAKE_DECLARED
    assert FAKE_DECLARED not in out


def test_placeholder_is_expanded_by_that_same_reader(tmp_path: Path, monkeypatch):
    """`${VAR:-default}` is resolved the way the gateway resolves it.

    If the script re-read the file itself and took the placeholder — or its
    default — literally, the reported fingerprint would be the default's, not the
    environment's.
    """
    config = write_config(
        tmp_path,
        "envref.yaml",
        "server:\n"
        f"  api_key: ${{{PLACEHOLDER_ENV}:-{FAKE_PLACEHOLDER_DEFAULT}}}  # inline comment\n",
    )
    # Same environment for the in-process reader and for the script's child, so
    # the comparison below is a comparison and not an artefact of who saw what.
    monkeypatch.setenv(PLACEHOLDER_ENV, FAKE_ROTATED)
    result = run_key_check(
        config,
        env_extra={PLACEHOLDER_ENV: FAKE_ROTATED, "AIGW_API_KEY": FAKE_ROTATED},
    )

    out = output_of(result)
    assert result.returncode == 0, out
    assert f"gateway_fp={fp(FAKE_ROTATED)}" in out
    assert f"gateway_fp={fp(FAKE_PLACEHOLDER_DEFAULT)}" not in out
    assert declared_by_the_shared_reader(config) == FAKE_ROTATED
    assert FAKE_PLACEHOLDER_DEFAULT not in out


def test_rotated_gateway_key_goes_red_even_with_no_env_override(tmp_path: Path):
    """The symptom this grid exists for: the gateway's key moved and the consumer
    still holds its own default.  That must be a red, not a 401."""
    config = write_config(tmp_path, "rotated.yaml", f"server:\n  api_key: {FAKE_ROTATED}\n")
    result = run_key_check(config)

    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=MISMATCH" in out
    assert "AIGW_KEY_VERDICT=MATCH" not in out
    assert f"gateway_fp={fp(FAKE_ROTATED)}" in out
    assert declared_by_the_shared_reader(config) == FAKE_ROTATED
    # Both sides named: which file the gateway declares in, which rung the script used.
    assert "server.api_key" in out
    assert str(config) in out
    assert "script_source=" in out
    assert FAKE_ROTATED not in out


def test_stale_env_override_goes_red_and_names_both_sources(tmp_path: Path):
    """An explicit env value that no longer matches the declaration goes red and
    names `env:AIGW_API_KEY` against the gateway's config, so the operator knows
    which of the two to fix."""
    config = write_config(tmp_path, "stale.yaml", f"server:\n  api_key: {FAKE_DECLARED}\n")
    result = run_key_check(config, env_extra={"AIGW_API_KEY": FAKE_STALE_ENV})

    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=MISMATCH" in out
    assert "script_source=env:AIGW_API_KEY" in out
    assert f"script_fp={fp(FAKE_STALE_ENV)}" in out
    assert f"gateway_fp={fp(FAKE_DECLARED)}" in out
    assert FAKE_DECLARED not in out
    assert FAKE_STALE_ENV not in out


def test_config_without_a_declaration_is_not_checked_and_never_a_pass(tmp_path: Path):
    """Nothing to reconcile against → NOT_CHECKED + non-zero.  "Cannot tell" must
    not be read as "agrees" (R-053 / 裁定 81)."""
    config = write_config(tmp_path, "nokey.yaml", "server:\n  host: 127.0.0.1\n  port: 8123\n")
    result = run_key_check(config, env_extra={"AIGW_API_KEY": FAKE_DECLARED})

    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=NOT_CHECKED" in out
    assert "NOT CHECKED" in out
    assert "AIGW_KEY_VERDICT=MATCH" not in out
    assert "aigw key ok" not in out
    assert declared_by_the_shared_reader(config) == ""


def test_empty_declaration_is_not_checked_and_not_called_a_mismatch(tmp_path: Path):
    """`server.api_key: ""` means "not configured yet" (裁定 94 ④), not "disagrees".

    The script inherits that reading because it *is* the Python reader's answer —
    that is the point of going through the shared source instead of imitating it:
    a second parser would have to re-decide this edge, and the one written in
    TypeScript called the same file a MISMATCH.
    """
    config = write_config(tmp_path, "empty.yaml", 'server:\n  api_key: ""\n')
    result = run_key_check(config, env_extra={"AIGW_API_KEY": FAKE_DECLARED})

    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=NOT_CHECKED" in out
    assert "AIGW_KEY_VERDICT=MISMATCH" not in out
    assert declared_by_the_shared_reader(config) == ""


def test_unreadable_gateway_config_is_not_checked(tmp_path: Path):
    """A config that cannot be decoded is a read failure, not a match."""
    config = write_config(tmp_path, "garbage.yaml", b"server:\n  api_key: \xff\xfe\x00broken\n")
    result = run_key_check(config, env_extra={"AIGW_API_KEY": FAKE_DECLARED})

    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=NOT_CHECKED" in out
    assert "AIGW_KEY_VERDICT=MATCH" not in out


def test_unreachable_declaration_reader_is_not_checked(tmp_path: Path):
    """No interpreter to ask → NOT_CHECKED, never "assume the fallback is fine".

    Uses a byte-identical copy of the script parked in a tree with no `.venv`,
    with PATH reduced to System32, so the reconcile cannot reach the reader at
    all.  Same source, so this exercises the real guard rather than a mock.
    """
    fake_root = tmp_path / "no-venv-checkout"
    script_copy = fake_root / "scripts" / "plobi" / "aigw_start.ps1"
    script_copy.parent.mkdir(parents=True)
    shutil.copyfile(_PS1, script_copy)
    config = write_config(fake_root, "cfg.yaml", f"server:\n  api_key: {FAKE_DECLARED}\n")

    result = run_key_check(
        config,
        env_extra={"AIGW_API_KEY": FAKE_DECLARED},
        script_path=script_copy,
        strip_path=True,
    )
    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=NOT_CHECKED" in out
    assert "AIGW_KEY_VERDICT=MATCH" not in out
    assert FAKE_DECLARED not in out


def test_silent_native_spawn_failure_is_not_checked(tmp_path: Path):
    """Windows PowerShell starts no native child when PATHEXT is missing, and it
    reports neither an error nor an exit code.

    Measured on this machine, not theorised: the reconcile used to see "no
    output, no exit code" and had to decide what that means.  It means the
    declaration is unreadable, so the verdict is NOT_CHECKED — the shape of a
    failure that would otherwise be indistinguishable from a pass.
    """
    config = write_config(tmp_path, "match.yaml", f"server:\n  api_key: {FAKE_DECLARED}\n")
    result = run_key_check(
        config, env_extra={"AIGW_API_KEY": FAKE_DECLARED}, drop_pathext=True
    )

    out = output_of(result)
    assert result.returncode != 0, out
    assert "AIGW_KEY_VERDICT=NOT_CHECKED" in out
    assert "AIGW_KEY_VERDICT=MATCH" not in out
    assert FAKE_DECLARED not in out


def test_no_hardcoded_bearer_literal_survives_in_the_script():
    """The bug class: a written-down key used as the probe's credential.

    Locked as an absence, because the fix is not "use a different literal" — the
    probe value now comes out of the reconciliation, and only its fingerprint is
    printed.
    """
    source = _PS1.read_text(encoding="utf-8")
    assert "sk-local-dev-key" not in source, (
        "aigw_start.ps1 carries a key literal again; the probe key must come "
        "from the gateway's own declaration (WP-AIGW-KEY-LITERALS)"
    )
    assert not re.search(r'Bearer\s+"sk-', source), (
        "the Authorization header must be built from the reconciled key, not "
        "from a literal starting with `sk-`"
    )
    assert re.search(r'Authorization\s*=\s*"Bearer \$Key"', source), (
        "Show-AigwModels should authenticate with the reconciled $Key it was "
        "handed, so the probe and the gateway cannot disagree silently"
    )


def test_script_reconciles_through_the_shared_reader_not_a_second_parser():
    """One way to read `server.api_key`, and it lives in plobi/agents/registry.py.

    The consumer families already nearly drifted over this (the Electron grid
    mirrors the Python reader on purpose); a third reading in PowerShell is the
    same defect wearing a different hat.  So: the script calls the shared reader,
    it never pattern-matches the key out of the file itself, and it keeps three
    verdicts — folding "could not read" into a pass is what this gate prevents.
    """
    source = _PS1.read_text(encoding="utf-8")
    for symbol in ("gateway_declared_api_key", "aigw_api_key", "aigw_credential_source"):
        assert symbol in source, f"expected the script to go through {symbol}()"
    offenders = [
        line
        for line in source.splitlines()
        if "api_key" in line and re.search(r"(-match|-like|\[regex\])", line)
    ]
    assert not offenders, (
        "aigw_start.ps1 is parsing server.api_key itself again; ask the gateway's "
        f"declared value instead: {offenders}"
    )
    for verdict in ("MATCH", "MISMATCH", "NOT_CHECKED"):
        assert f"AIGW_KEY_VERDICT={verdict}" in source, (
            f"verdict {verdict} is missing from the reconcile output; a two-way "
            "verdict turns 'could not read' into a silent pass"
        )

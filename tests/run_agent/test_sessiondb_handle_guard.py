"""WP-WIN-SESSIONDB-HANDLE 必做 4：拦住「开了不关」回归的守卫。

两条，都是不变量而不是名单快照：

1. **忘关在这台 Windows 上必须立刻红**，而且 POSIX 上必须不红。这条同时钉住两件事：
   这些用例为什么在开发机上永远绿（POSIX 允许 unlink 打开着的文件），以及 R-043 之后
   为什么「上游绿」不能当证据。如果哪天 Windows 上不抛了，说明平台语义变了、夹具里的
   `finally: db.close()` 成了装饰 —— 那条要红给人看，不许悄悄同意。
2. 已修的两个文件里，`SessionDB` 不许再绕开夹具自己构造。这是形状检查，不是行号快照。

必做 5 的同类清单（测试 / 运行期 / 一次性脚本三族）**不在这里断言**：任务书要求它「只报不修」，
写成断言会凭空造出一批红；清单交在 Docs 流水里。平台相关断言一律 gate，不写 chmod、
不写符号链接、不写 `/opt`（裁定 79）。
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXED_FILES = (
    REPO_ROOT / "tests" / "run_agent" / "test_in_place_compaction.py",
    REPO_ROOT / "tests" / "run_agent" / "test_compression_boundary_hook.py",
)


def test_leaving_the_handle_open_blocks_cleanup_exactly_on_windows():
    """A SessionDB opened inside a TemporaryDirectory and never closed: red on
    Windows, green on POSIX. That asymmetry is the whole reason this family was
    invisible for so long, so the test asserts both halves of it."""
    from plobi_state import SessionDB

    made_dir = []
    held = []
    raised = None
    try:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                made_dir.append(tmp)
                held.append(SessionDB(db_path=Path(tmp) / "t.db"))
                # Deliberately not closed -- exiting the block is the experiment.
        except PermissionError as exc:
            raised = exc

        if sys.platform == "win32":
            assert raised is not None, (
                "Windows 上没有因为漏关而红：平台语义变了，"
                "`_tmp_session_db` 里的 finally close 从此只是装饰，必须重新定这条刀的判据"
            )
            assert "t.db" in str(raised) or "另一个程序" in str(raised)
        else:
            assert raised is None, "POSIX 上这里应当删得掉；它红了就是另一回事，别混进本刀"
    finally:
        for db in held:
            db.close()
        # Cleanup after the handle is closed. Not a stand-in for the forbidden
        # `ignore_cleanup_errors` shape: the failure we want to keep visible is
        # raised and asserted above, before anything is tidied here.
        for path in made_dir:
            if Path(path).exists():
                shutil.rmtree(path, ignore_errors=True)


def _fixture_body_lines(lines):
    """行号集合：夹具函数自己的身体。那是唯一允许直接构造 SessionDB 的地方。"""
    allowed = set()
    inside = None
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("def _tmp_session_db("):
            inside = len(line) - len(line.lstrip())
            allowed.add(lineno)
            continue
        if inside is None:
            continue
        if not stripped:
            allowed.add(lineno)
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= inside:
            inside = None
            continue
        allowed.add(lineno)
    return allowed


@pytest.mark.parametrize("target", FIXED_FILES, ids=lambda p: p.name)
def test_repaired_files_never_construct_sessiondb_outside_the_fixture(target):
    """Every SessionDB in the repaired files must come from the closing fixture."""
    assert target.exists(), f"{target.name} 不在了——改名或搬走要连这条守卫一起改"
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert "_tmp_session_db(" in text, f"{target.name}: 夹具不见了，回到「谁开谁关」之前"

    allowed = _fixture_body_lines(lines)
    offenders = [
        lineno
        for lineno, line in enumerate(lines, 1)
        if re.search(r"\bSessionDB\(", line)
        and lineno not in allowed
        and "import" not in line  # 局部 `from plobi_state import SessionDB` 不是构造
    ]
    assert not offenders, (
        f"{target.name} 在夹具之外构造了 SessionDB（行 {offenders}）——"
        "退出临时目录时没人关它，Windows 上会把 rmtree 撞成 PermissionError"
    )

"""发版前那道闸的测试：workflow 引用的仓内脚本，必须在要发的那个提交里存在。

背景（2026-11-07 现场）：一笔提交把在途未提交的 chatlog 作业带进了仓，而那个作业引用的
``apps/desktop/scripts/build-chatlog.mjs`` 只在盘上、不在 git 里 ⇒ 三平台构建全红、任何发版
都被拦。这道检查就是把那次事故变成机器能判的一条。
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_check_module():
    path = REPO_ROOT / "scripts" / "plobi" / "check_workflow_scripts.py"
    spec = importlib.util.spec_from_file_location("_check_workflow_scripts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _head() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_a_script_that_is_in_the_commit_is_not_reported():
    module = _load_check_module()
    commit = _head()
    workflow = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: node scripts/plobi/check_test_baseline.py --from-log log\n"
    )
    assert module._missing_in_workflow(workflow, commit) == []


def test_a_script_that_is_missing_from_the_commit_is_reported():
    module = _load_check_module()
    commit = _head()
    workflow = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: node scripts/plobi/this_script_is_not_in_git.py\n"
    )
    missing = module._missing_in_workflow(workflow, commit)
    assert missing == ["scripts/plobi/this_script_is_not_in_git.py"], missing


def test_a_bare_file_name_is_not_treated_as_a_repo_path():
    """`Node.js`、`install.ps1` 这类裸文件名不是仓内路径（它们只是词）。"""
    module = _load_check_module()
    commit = _head()
    workflow = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: node --version   # Node.js 20\n"
        "      - run: ./install.ps1\n"
        "      - run: ./install.sh\n"
    )
    assert module._missing_in_workflow(workflow, commit) == []


def test_the_check_is_green_on_head():
    """当前这个提交里，每个 workflow 引用的仓内脚本都在。"""
    module = _load_check_module()
    assert module.missing_scripts(_head()) == []

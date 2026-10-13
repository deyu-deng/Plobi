"""就地更新的 git 那条路：只快进、脏树绝不碰、拉不到就说拉不到（R-046）。

用真 git 仓（bare 远端 + 两个克隆），不联网：远端就是 tmp_path 下另一个目录。
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from plobi_cli.main import cmd_update
from plobi_cli.self_update import (apply_git_update, git_update_state,
                                   local_self_update_applies)


def _git(cwd, *args):
    r = subprocess.run(['git', '-c', 'user.email=t@example.com', '-c', 'user.name=T',
                        '-c', 'commit.gpgsign=false', *args],
                       cwd=str(cwd), capture_output=True, text=True, errors='replace')
    assert r.returncode == 0, f'git {args} 失败：{r.stdout}{r.stderr}'
    return (r.stdout or '').strip()


def _advance(remote, work, name):
    """在第二个克隆里加一个提交并推上去 —— 让 work 落后，而不去碰它。"""
    other = work.parent / f'other-{name}'
    _git(work.parent, 'clone', str(remote), other.name)
    (other / f'{name}.txt').write_text(name, encoding='utf-8')
    _git(other, 'add', '-A')
    _git(other, 'commit', '-m', name)
    _git(other, 'push', 'origin', 'main')
    return _git(other, 'rev-parse', '--short=8', 'HEAD')


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-b', 'main', str(remote)],
                   capture_output=True, text=True, check=True)
    work = tmp_path / 'work'
    _git(tmp_path, 'clone', str(remote), 'work')
    (work / 'seed.txt').write_text('seed', encoding='utf-8')
    _git(work, 'add', '-A')
    _git(work, 'commit', '-m', 'seed')
    _git(work, 'push', '-u', 'origin', 'main')
    return remote, work


class TestPreconditionsAreSeparatelyReportable:
    def test_clean_and_behind_is_the_only_applies_state(self, repo, tmp_path):
        remote, work = repo
        _advance(remote, work, 'one')

        state = git_update_state(work, fetch=True)

        assert state['applies'] is True, state
        assert state['reason'] == 'ok'
        assert state['behind'] == 1 and state['ahead'] == 0
        assert state['remote'] == 'origin' and state['branch'] == 'main'

    def test_up_to_date_says_so_rather_than_pretending(self, repo):
        _, work = repo

        state = git_update_state(work, fetch=True)

        assert state['reason'] == 'up_to_date'
        assert state['applies'] is False and state['behind'] == 0

    def test_dirty_tree_is_refused_and_not_stashed(self, repo, tmp_path):
        remote, work = repo
        _advance(remote, work, 'two')
        (work / 'local.txt').write_text('别人的未提交改动', encoding='utf-8')
        _git(work, 'add', 'local.txt')
        before = _git(work, 'rev-parse', 'HEAD')

        state = git_update_state(work, fetch=True)
        result = apply_git_update(work)

        assert state['reason'] == 'working_tree_dirty' and state['dirty_files'] == 1
        assert result['ok'] is False and result['reason'] == 'working_tree_dirty'
        assert _git(work, 'rev-parse', 'HEAD') == before, '按不动的时候 HEAD 不许动'
        assert (work / 'local.txt').exists(), '未提交的行被抹掉了'

    def test_unpushed_local_commit_refuses_instead_of_rebasing(self, repo):
        _, work = repo
        (work / 'mine.txt').write_text('1', encoding='utf-8')
        _git(work, 'add', '-A')
        _git(work, 'commit', '-m', '本地还没推')

        state = git_update_state(work, fetch=True)

        assert state['reason'] == 'diverged' and state['ahead'] == 1
        assert state['applies'] is False

    def test_detached_head_has_no_fast_forward_target(self, repo):
        _, work = repo
        _git(work, 'checkout', '--detach')

        assert git_update_state(work, fetch=False)['reason'] == 'detached_head'

    def test_no_remote_is_named_not_guessed(self, repo):
        _, work = repo
        _git(work, 'remote', 'remove', 'origin')

        assert git_update_state(work, fetch=False)['reason'] == 'no_remote'

    def test_unreachable_remote_says拉不到_and_never_falls_back(self, repo, monkeypatch):
        _, work = repo
        _git(work, 'remote', 'set-url', 'origin', str(work.parent / '没有这么个仓'))

        state = git_update_state(work, fetch=True)

        assert state['reason'] == 'fetch_failed'
        assert state['applies'] is False and state['behind'] is None

    def test_remote_override_must_name_a_real_remote(self, repo, monkeypatch):
        _, work = repo
        monkeypatch.setenv('PLOBI_UPDATE_REMOTE', 'ghost')

        assert git_update_state(work, fetch=False)['reason'] == 'no_remote'


class TestApplyActuallyMovesTheCheckout:
    def test_apply_fast_forwards_and_reports_both_ends(self, repo):
        remote, work = repo
        after = _advance(remote, work, 'three')
        before = _git(work, 'rev-parse', '--short=8', 'HEAD')

        result = apply_git_update(work)

        assert result['ok'] is True, result
        assert result['head'] == before and result['after'] == after
        assert result['behind'] == 1
        assert (work / 'three.txt').exists(), '快进没真的落到工作树'
        assert _git(work, 'status', '--porcelain') == ''

    def test_cli_and_dashboard_share_one_judgement(self, repo, monkeypatch, capsys):
        """判据只有一处：CLI 与仪表盘不许各写一份（R-046 的成因就是抄本）。"""
        remote, work = repo
        _advance(remote, work, 'four')
        monkeypatch.setattr('plobi_cli.config.detect_install_method', lambda _: 'git')
        monkeypatch.setattr('plobi_cli.config.get_project_root', lambda: work)

        rc = cmd_update(SimpleNamespace(check=False))
        out = capsys.readouterr().out

        assert rc == 0
        assert '更新已落地' in out and 'four.txt' not in out
        assert (work / 'four.txt').exists()

    def test_cli_refusal_exits_nonzero_instead_of_lieing(self, repo, monkeypatch, capsys):
        _, work = repo
        (work / 'dirty.txt').write_text('未提交', encoding='utf-8')
        monkeypatch.setattr('plobi_cli.config.detect_install_method', lambda _: 'git')
        monkeypatch.setattr('plobi_cli.config.get_project_root', lambda: work)

        rc = cmd_update(SimpleNamespace(check=False))

        assert rc == 1, '按不动却返回 0 = 把空转记成成功'
        assert '未提交' in capsys.readouterr().out


class TestOtherInstallMethods:
    def test_pip_still_applies_and_docker_never_does(self, repo):
        _, work = repo
        assert local_self_update_applies('pip') is True
        assert local_self_update_applies('docker') is False
        assert local_self_update_applies('nix', work) is False

    def test_git_can_apply_flag_tracks_the_worktree(self, repo):
        _, work = repo
        assert local_self_update_applies('git', work) is False, '没 fetch 前不该谎报能更新'
        (work / 'x.txt').write_text('1', encoding='utf-8')
        _git(work, 'add', '-A')
        _git(work, 'commit', '-m', 'local')
        assert local_self_update_applies('git', work) is False

"""裁 178 乙案 第 4 步：`_profile_runtime_scope` 不得复活一个已经不存在的家。

现场（10-25 15:14 实测）：24 家已在 12:0x 整屋搬进 `_quarantine-20261024/`，之后
`profiles/l2-agenda/` 里又长出一本**空的 `state.db`**（表齐全、sessions=0）。
成因是这一格——`set_plobi_home_override(那个家)` 一生效，`get_plobi_home()` 就指过去，
后面任何一处按解析到的家开库/建目录，都会把退役的家原地建回来。不报错、不告警。

这里锁住的性质（不引入任何按分身分叉的新路径）：
  * 家不在盘上 ⇒ 折回共享根，那个家**一个字节都不许多出来**；
  * 家还在盘上 ⇒ 逐位照旧（multiplexer 的既有隔离语义不动，三档老测试不改）；
  * 折回不能把密钥那一腿一起丢掉（按给定路径建 scope，家没了就是空 scope）。
"""

from __future__ import annotations

import sqlite3

from agent import secret_scope as ss
from plobi_constants import get_plobi_home


def _root(tmp_path):
    """A shared root with a `profiles/` dir that holds NO homes (all retired)."""
    root = tmp_path / "plobi-home"
    (root / "profiles").mkdir(parents=True)
    return root


class TestMissingHomeFallsBack:
    def test_home_absent_scopes_to_shared_root(self, tmp_path):
        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        ghost = root / "profiles" / "l2-agenda"

        with _profile_runtime_scope(ghost):
            seen = get_plobi_home()

        assert seen == root, "家不在 ⇒ 这一轮该落在共享根上"
        assert not ghost.exists(), "切到一个不存在的家，不该把它建回来"

    def test_existing_home_still_scopes_to_itself(self, tmp_path):
        """Multiplex 的既有语义：家在场时逐位照旧，不收窄也不放宽。"""
        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        prof_a = root / "profiles" / "profA"
        prof_b = root / "profiles" / "profB"
        for p in (prof_a, prof_b):
            p.mkdir(parents=True)

        with _profile_runtime_scope(prof_a):
            a_seen = get_plobi_home()
        with _profile_runtime_scope(prof_b):
            b_seen = get_plobi_home()

        assert a_seen == prof_a
        assert b_seen == prof_b

    def test_non_profile_home_is_left_alone(self, tmp_path):
        """不在 `profiles/` 下面的家（活跃家/装好的家）没有"共享根"可折——照旧。"""
        from gateway.run import _profile_runtime_scope

        elsewhere = tmp_path / "some" / "other-home"
        with _profile_runtime_scope(elsewhere):
            assert get_plobi_home() == elsewhere

    def test_scope_leaves_no_override_behind(self, tmp_path):
        from gateway.run import _profile_runtime_scope
        from plobi_constants import get_plobi_home_override

        root = _root(tmp_path)
        before = get_plobi_home_override()
        with _profile_runtime_scope(root / "profiles" / "gone"):
            pass
        assert get_plobi_home_override() == before


class TestFallbackDoesNotResurrectThroughRealWriters:
    """折回要挡住的是**真实写者**，不只是那一句 override。

    这三处是当年把退役的家补回来的腿（`plobi_cli/config.py:1069`、
    `gateway/session.py:1054`、以及按解析到的家开库的那一步）。
    """

    def test_seeder_writes_root_not_the_ghost(self, tmp_path):
        from gateway.run import _profile_runtime_scope
        from plobi_cli.config import ensure_plobi_home

        root = _root(tmp_path)
        ghost = root / "profiles" / "l2-agenda"

        with _profile_runtime_scope(ghost):
            ensure_plobi_home()

        assert not ghost.exists()
        assert (root / "sessions").is_dir()

    def test_session_store_dir_follows_the_fallback(self, tmp_path):
        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        ghost = root / "profiles" / "l2-agenda"

        with _profile_runtime_scope(ghost):
            sessions_dir = get_plobi_home() / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)

        assert sessions_dir == root / "sessions"
        assert not ghost.exists()

    def test_state_db_opens_at_the_root_not_the_ghost(self, tmp_path):
        """10-25 那本空 `state.db` 的正面回归：按解析到的家开库 ⇒ 落在根上。"""
        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        ghost = root / "profiles" / "l2-agenda"

        with _profile_runtime_scope(ghost):
            db = get_plobi_home() / "state.db"
            with sqlite3.connect(str(db)) as conn:
                conn.execute("CREATE TABLE sessions (id TEXT)")
                conn.commit()

        assert db == root / "state.db"
        assert db.exists()
        assert not ghost.exists(), "退役的家里不许长出一本新库"


class TestSecretScopeFollowsTheSameHome:
    """折回是**两条腿一起**折：只折 config 会让这一轮拿到一个空 scope。

    空 scope 不是"没装 scope"——`get_secret` 见到装了的 scope 就不再回落 `os.environ`，
    于是每一次凭据读取都静默返回 None（比复活一个空壳更难查）。这条锁死那个形状。
    """

    def test_existing_home_secrets_still_scope(self, tmp_path):
        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        prof = root / "profiles" / "profA"
        prof.mkdir(parents=True)
        (prof / ".env").write_text("MY_PROFILE_KEY=sk-A\n", encoding="utf-8")

        with _profile_runtime_scope(prof):
            assert ss.get_secret("MY_PROFILE_KEY") == "sk-A"

    def test_missing_home_falls_back_to_shared_root_secrets(self, tmp_path):
        import os

        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        (root / ".env").write_text("PLOBI_TEST_FALLBACK_KEY=top-level-value\n", encoding="utf-8")
        ghost = root / "profiles" / "l2-agenda"
        # 对照：进程环境里那一份**不是**这里的依据——scope 装了就以 scope 为准。
        os.environ["PLOBI_TEST_FALLBACK_KEY"] = "ambient-value"
        try:
            with _profile_runtime_scope(ghost):
                assert ss.current_secret_scope() == {"PLOBI_TEST_FALLBACK_KEY": "top-level-value"}
                assert ss.get_secret("PLOBI_TEST_FALLBACK_KEY") == "top-level-value"
        finally:
            os.environ.pop("PLOBI_TEST_FALLBACK_KEY", None)
        assert not ghost.exists()

    def test_two_existing_homes_never_see_each_other(self, tmp_path):
        """既有隔离性质在改动后仍然成立（这条不许因为折回而退化）。"""
        from gateway.run import _profile_runtime_scope

        root = _root(tmp_path)
        prof_a = root / "profiles" / "profA"
        prof_b = root / "profiles" / "profB"
        for p, key in ((prof_a, "sk-A"), (prof_b, "sk-B")):
            p.mkdir(parents=True)
            (p / ".env").write_text(f"SHARED_NAME={key}\n", encoding="utf-8")

        with _profile_runtime_scope(prof_a):
            a_val = ss.get_secret("SHARED_NAME")
        with _profile_runtime_scope(prof_b):
            b_val = ss.get_secret("SHARED_NAME")

        assert (a_val, b_val) == ("sk-A", "sk-B")

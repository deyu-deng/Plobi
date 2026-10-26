"""裁定 203 那四条口径的机械判据（信源配置这一族）。

这一档不测"某条恰好填了什么"，测的是**形状**：

1. 每一格用户要填的东西，旁边都得有一句指路（`help` 非空）——缺了它，屏幕上就是一个空框加一个标签；
2. 那句指路**不许点到文件名、环境变量名、命令行**（那是把一步藏进"你该自己去知道"）；
3. 凭据读口的优先序：**界面那一格写的键优先，历史键名只读回退**；只配过历史键的人
   不许被念成"还没配"（那是一颗按了回到原地的开关）；
4. 那把"用户记不住的"密钥由产品自己取：取到没取到只回一句话，**那句话里永远没有密钥**；
   取不到要说为什么，不许退化成"你去填一格"；
5. 准备那一步失败或抛异常，都不许把用户那次点开关变成 500。
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from plobi.collectors import registry, sources_config
from plobi.collectors.chatlog import keyhand, sidecar as wx
from plobi.collectors.zju import auth

registry.ensure_discovered()

#: 屏幕上那句话不许出现的东西（与 `scripts/check_status_copy_coverage.py` 同一族判据，
#: 那一支管四语目录里的句子，这一支管声明里的英文缺省值）。
ARTIFACTS = ("PLOBI_", "CHATLOG_", "ZJU_", "agenda.db", "pool.db", ".env", "go build", "`")


class TestEveryFieldHasAHint:
    @pytest.mark.parametrize("source_id", sorted(registry.source_ids()))
    def test_declared_fields_carry_a_hint_without_artifacts(self, source_id: str) -> None:
        for field in registry.spec(source_id).setup_fields:
            assert field.help.strip(), f"{source_id}.{field.key} 这格没有指路的话"
            for artifact in ARTIFACTS:
                assert artifact not in field.help, f"{source_id}.{field.key} 的指路话点了 {artifact!r}"

    def test_our_own_credentials_came_back_into_the_ui(self) -> None:
        """教务两家的账密**在界面上填**（裁定 203 第 1 条：只有用户知道的信息才进界面）。

        10-23 删掉的是"没人读的两格"，不是这个；现在这两格是采集器真读的入口。
        """
        for source_id in ("zju", "zdbk"):
            keys = [f.key for f in registry.spec(source_id).setup_fields]

            assert "password" in keys and "username" in keys
            assert registry.spec(source_id).credential_aliases.get("password") == ("ZJU_PASSWORD",)


class TestCredentialPrecedence:
    def test_the_new_writables_win_and_legacy_is_read_only_fallback(self, monkeypatch) -> None:
        from plobi.collectors import zju as zju_pkg

        reads: dict[str, str] = {}

        monkeypatch.setattr(sources_config, "setup_params",
                            lambda sid: {"username": reads.get("username", "")})
        monkeypatch.setattr(sources_config, "secret_value",
                            lambda sid, key: reads.get(key))

        reads.update({"username": "界面写的", "password": "界面写的密"})
        assert auth.read_credentials("zju") == ("界面写的", "界面写的密")

        # 只有历史键那一面：仍读得到（不许逼人家重填一遍）
        monkeypatch.setattr("plobi_cli.config.get_env_value_prefer_dotenv",
                            lambda name: {"ZJU_USERNAME": "老键账号", "ZJU_PASSWORD": "老键密码"}[name])
        reads.clear()
        assert auth.read_credentials("zju") == ("老键账号", "老键密码")

        reads.update({"username": "界面写的", "password": "界面写的密"})
        assert auth.read_credentials("zju") == ("界面写的", "界面写的密"), "界面上改了却被旧值盖住＝改了没生效"

        assert zju_pkg.SPEC.source_id == "zju"

    def test_only_legacy_keys_still_count_as_configured(self, monkeypatch) -> None:
        """`secret_configured` 也算回退名单：否则他早就配过的那一条会被念成"还没配"。"""
        env = {"ZJU_PASSWORD": "老键密码", "PLOBI_SOURCE_OTHER_THING": "x"}

        monkeypatch.setattr("plobi_cli.config.load_env", lambda: env)

        assert sources_config.secret_configured("zdbk") == {"password": True}
        assert sources_config.secret_configured("timetable") == {}

    def test_a_fresh_install_says_not_configured(self, monkeypatch) -> None:
        monkeypatch.setattr("plobi_cli.config.load_env", lambda: {})

        assert sources_config.secret_configured("zju") == {"password": False}


def _no_key_here(env: dict[str, str]) -> dict[str, str]:
    return {**env, "CHATLOG_DATA_KEY": "", "CHATLOG_DIR": ""}


class TestTheKeyHand:
    def test_an_existing_key_does_not_touch_the_process(self, tmp_path, monkeypatch) -> None:
        called: list[list[str]] = []

        ok, note = keyhand.fetch({"CHATLOG_DATA_KEY": "already-here"},
                                 runner=lambda argv, **kw: called.append(argv))

        assert ok and called == []
        assert "已经有" in note

    def test_a_missing_binary_says_what_is_missing_without_naming_env_vars(self, monkeypatch) -> None:
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: None)

        ok, note = keyhand.fetch(_no_key_here({}), runner=lambda *a, **kw: pytest.fail("不该跑命令"))

        assert not ok
        assert "可执行产物" in note
        for artifact in ARTIFACTS:
            assert artifact not in note, "取不到时也不许把文件名/变量名塞回给他"

    def test_a_recovered_key_lands_in_the_handoff_and_never_in_the_sentence(self, tmp_path, monkeypatch) -> None:
        secret = "0123456789abcdef0123456789abcdef"
        directory = tmp_path / "engine"
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: directory)

        def fake_run(argv, **kwargs):
            assert argv[-1] == "key", f"该跑的是产物自带的 key 子命令，实际 {argv}"
            return SimpleNamespace(stdout=f"data key: {secret}\n", stderr="")

        ok, note = keyhand.fetch(_no_key_here({}), runner=fake_run)

        assert ok is True
        assert secret not in note, "返回值就是那一句上屏的话——密钥不许搭这趟车"
        landed = json.loads((directory / "chatlog.json").read_text(encoding="utf-8"))
        assert landed["data_key"] == secret

    def test_a_silent_run_reports_why_instead_of_asking_him_to_type_it(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path)

        ok, note = keyhand.fetch(_no_key_here({}),
                                 runner=lambda *a, **kw: SimpleNamespace(stdout="", stderr="weixin not running"))

        assert not ok and "没从微信里取到" in note
        assert "填" not in note, "取不到就明说取不到——不许退化成让他抄一串"

    def test_a_broken_run_is_a_reading_not_a_crash(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path)

        def boom(*a, **kw):
            raise OSError("杀进程被拒")

        ok, note = keyhand.fetch(_no_key_here({}), runner=boom)

        assert not ok and "OSError" in note


class TestThePrepareStepIsInvisibleToTheClick:
    @staticmethod
    def _spec(prepare):
        return SimpleNamespace(sidecar_port=5099, sidecar_start="scripts/whatever.ps1",
                               sidecar_prepare=prepare)

    def test_a_failing_preparation_still_returns_a_reading(self) -> None:
        from plobi.collectors import sidecar as common

        result = common.apply(self._spec(lambda: (False, "没取到密钥，因为微信没在跑")),
                              enabled=True, listening=lambda _p: False,
                              finder=lambda _p: [], killer=lambda _p: True,
                              spawn=lambda _s: 4242, wait_seconds=0.0)

        assert result["action"] == "started_not_listening"
        assert "没取到密钥" in result["prepare"]

    def test_a_throwing_preparation_does_not_blow_up_the_click(self) -> None:
        from plobi.collectors import sidecar as common

        def explode() -> tuple[bool, str]:
            raise RuntimeError("里面的东西坏了")

        result = common.apply(self._spec(explode), enabled=True, listening=lambda _p: False,
                              finder=lambda _p: [], killer=lambda _p: True,
                              spawn=lambda _s: 4242, wait_seconds=0.0)

        assert result["action"] in ("started", "started_not_listening")
        assert "准备那一步没做成" in result["prepare"], "抛异常要收成一句读数，不是 500"

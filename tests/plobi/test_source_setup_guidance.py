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
import time
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


class _Proc:
    """形状 = `subprocess.Popen` 那一小面（`wait` / `stdout` / `stderr`），只够这一档用。

    `hang=True` 复现 10-26 真机量到的那一等（扫描超过我们给的耐心上限）；
    `killed` 记的是有没有人动过杀/终止——那一条是"不许白扫"的判据。
    """

    def __init__(self, out: str = "", err: str = "", *, hang: bool = False) -> None:
        self.stdout = out
        self.stderr = err
        self.hang = hang
        self.killed = 0

    def wait(self, timeout: float | None = None) -> int:
        if self.hang:
            raise subprocess.TimeoutExpired(["chatlog.exe", "key"], timeout or 0)
        return 0

    def terminate(self) -> None:
        self.killed += 1

    def kill(self) -> None:
        self.killed += 1


def _launcher(proc: _Proc, seen: list[dict] | None = None):
    def launch(argv, **kwargs):
        if seen is not None:
            seen.append({"argv": argv, **kwargs})
        # 那两个流在这里是字符串，`keyhand._drain` 只调 `.read()`
        return SimpleNamespace(stdout=SimpleNamespace(read=lambda: proc.stdout),
                               stderr=SimpleNamespace(read=lambda: proc.stderr),
                               wait=proc.wait, terminate=proc.terminate, kill=proc.kill)

    return launch


DATA_KEY = "0123456789abcdef0123456789abcdef"
IMG_KEY = "ffeeddccbbaa99887766554433221100"


class TestTheKeyHand:
    def test_an_existing_key_does_not_touch_the_process(self, tmp_path, monkeypatch) -> None:
        launched: list[str] = []

        def spy(argv, **kwargs):
            launched.append(argv[0])
            return _Proc()

        ok, note = keyhand.fetch({"CHATLOG_DATA_KEY": "already-here"}, launcher=spy)

        assert ok and launched == []
        assert "已经有" in note

    def test_a_missing_binary_does_not_launch_anything(self, monkeypatch) -> None:
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: None)

        ok, note = keyhand.fetch(_no_key_here({}),
                                 launcher=lambda *a, **kw: pytest.fail("不该起进程"))

        assert not ok
        assert "可执行产物" in note
        for artifact in ARTIFACTS:
            assert artifact not in note, "取不到时也不许把文件名/变量名塞回给他"

    def test_the_real_success_shape_is_the_labelled_bracketed_key(self, tmp_path, monkeypatch) -> None:
        """`manager.go:269` 打的是 `Data Key: [..]`，不是光一串十六进制。

        只认裸串的那一版会把**真取到了**报成没取到——屏幕上就出现一句假故障。
        """
        directory = tmp_path / "engine"
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: directory)

        ok, note = keyhand.fetch(
            _no_key_here({}),
            launcher=_launcher(_Proc(f"Data Key: [{DATA_KEY}]\nImage Key: [{IMG_KEY}]")))

        assert ok is True
        assert DATA_KEY not in note and IMG_KEY not in note, "返回值就是那一句上屏的话"
        landed = json.loads((directory / "chatlog.json").read_text(encoding="utf-8"))
        assert landed["data_key"] == DATA_KEY, "只认 Data Key 那一串"

    def test_a_key_wrapped_in_ansi_escapes_is_still_a_key(self, tmp_path, monkeypatch) -> None:
        """10-26 真机现核：它每一行都裹着 ANSI 颜色码。脱色前匹配 ⇒ 真密钥一律漏掉。"""
        directory = tmp_path / "engine"
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: directory)

        ok, note = keyhand.fetch(
            _no_key_here({}),
            launcher=_launcher(_Proc(f"\x1b[32mData Key: [{DATA_KEY}]\x1b[0m")))

        assert ok is True, note
        assert json.loads((directory / "chatlog.json").read_text(encoding="utf-8"))["data_key"] == DATA_KEY

    def test_a_config_dump_line_is_never_quoted_back_at_him(self, tmp_path, monkeypatch) -> None:
        """它失败时也把整份配置打在屏幕上（真机现核到那一句）。那一行里就有密钥本身。"""
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path)
        dump = ('\x1b[31mERRO\x1b[0m tui config: {"conf": {"data_key": ""}, "work_dir": '
                '"C:/Users/x/.chatlog"}\n\x1b[31mERRO\x1b[0m failed to get key\n')

        ok, note = keyhand.fetch(_no_key_here({}), launcher=_launcher(_Proc("", dump)))

        assert not ok
        assert "data_key" not in note and "{" not in note, "转储那一行一个字都不许上屏"
        assert "failed to get key" in note, "挑得到安全的一句就用它——那句才是他看得懂的为什么"

    def test_a_run_with_no_shape_at_all_says_why_instead_of_asking_him_to_type_it(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path)

        ok, note = keyhand.fetch(_no_key_here({}), launcher=_launcher(_Proc("", "weixin not online")))

        assert not ok and "没从微信里取到" in note
        assert "填" not in note, "取不到就明说取不到——不许退化成让他抄一串"

    def test_two_wechats_running_is_a_real_reason_not_a_dead_button(
        self, tmp_path, monkeypatch
    ) -> None:
        """`manager.go:279` 多实例时打的是那句候选名单——它带着路径与方括号，不能照抄。"""
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path)
        listing = ("Select a process:\n"
                   f"PID: 1234. Weixin[Version: 4 Data Dir: D:/x/{DATA_KEY} ]\n")

        ok, note = keyhand.fetch(_no_key_here({}), launcher=_launcher(_Proc(listing)))

        assert not ok and "好几个微信" in note and "再点一次开关" in note
        assert DATA_KEY not in note

    def test_a_slow_scan_is_left_running_instead_of_being_killed(
        self, tmp_path, monkeypatch
    ) -> None:
        """真机量到一次三分多钟没回来的扫描（`wechat.go:67` 自己没有截止）。

        杀掉 = 白扫 = 这条自动路在这台机器上永远取不到；所以等不到就转后台，
        屏幕上那句要说清"还在扫"，而不是"你去填一格"。
        """
        proc = _Proc(hang=True)
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path / "engine")

        ok, note = keyhand.fetch(_no_key_here({}), launcher=_launcher(proc), timeout=0.01)

        assert not ok
        assert note == keyhand.STILL_RUNNING
        assert "填" not in note
        assert proc.killed == 0, "等不到不等于杀掉——杀掉就是白扫"

    def test_the_background_run_still_lands_the_key_after_the_wait_gave_up(
        self, tmp_path, monkeypatch
    ) -> None:
        directory = tmp_path / "engine"
        proc = _Proc(f"Data Key: [{DATA_KEY}]", hang=True)
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: directory)

        keyhand.fetch(_no_key_here({}), launcher=_launcher(proc), timeout=0.01)
        landed = directory / "chatlog.json"
        for _ in range(200):
            if landed.is_file():
                break
            time.sleep(0.01)

        assert json.loads(landed.read_text(encoding="utf-8"))["data_key"] == DATA_KEY

    def test_the_child_is_told_where_its_own_config_lives(
        self, tmp_path, monkeypatch
    ) -> None:
        """没钉 `CHATLOG_DIR` ⇒ 产物回退 `C:\\Users\\<user>\\.chatlog`：既落 C 盘（裁定 109 三），
        又把它写的地方和我们读的地方劈成两处（写了等于没写）。"""
        seen: list[dict] = []
        directory = tmp_path / "engine"
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: directory)

        keyhand.fetch(_no_key_here({}), launcher=_launcher(_Proc(), seen), timeout=1)

        assert seen[0]["env"]["CHATLOG_DIR"] == str(directory)

    def test_a_key_the_tool_persisted_itself_counts_as_a_win(
        self, tmp_path, monkeypatch
    ) -> None:
        """屏幕上要的只是"本机现在解得解开吗"，不是"那句话是不是我打印的"。

        `manager.go:266` 那次 `UpdateConfig` 就在钉给它的目录里落盘；这一支不解析 stdout，
        所以 stdout 完全可以是一句报错的样子（版本不同形状不同），只要文件里有值就别说失败。
        """
        directory = tmp_path / "engine"
        env = _no_key_here({})

        def launch(argv, **kwargs):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "chatlog.json").write_text(json.dumps({"data_key": DATA_KEY}),
                                                    encoding="utf-8")
            return SimpleNamespace(stdout=SimpleNamespace(read=lambda: ""),
                                   stderr=SimpleNamespace(read=lambda: ""),
                                   wait=lambda timeout=None: 0)

        monkeypatch.setattr(keyhand, "resolved_binary", lambda e: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda e: directory)
        monkeypatch.setattr(wx, "engine_config_dir", lambda e: directory)

        ok, note = keyhand.fetch(env, launcher=launch)

        assert ok is True, note
        assert DATA_KEY not in note

    def test_a_process_that_cannot_start_is_a_reading_not_a_crash(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(keyhand, "resolved_binary", lambda env: str(tmp_path / "chatlog.exe"))
        monkeypatch.setattr(keyhand, "engine_config_dir", lambda env: tmp_path)

        def boom(argv, **kwargs):
            raise OSError("杀进程被拒")

        ok, note = keyhand.fetch(_no_key_here({}), launcher=boom)

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

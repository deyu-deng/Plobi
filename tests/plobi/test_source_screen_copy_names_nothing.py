"""屏幕上那句话不许说出文件名、环境变量名、命令行（裁定 203 第 1 条）。

微信这一条今天有四种不同的病（缺构建工具／没产物／没密钥／服务不应答），
10-25 之前它们在屏幕上并成一句「采集服务未能启动」，而分开说的那四句里带着
`PLOBI_CHATLOG_BIN`、`go build` 这类**只有开发者记得住的名字**——那等于把一步藏进
"你该自己去知道"。这一档钉两件事：**四种病分得开**，**每句话里不许出现那些名字**。
"""

import re

import pytest

from plobi.collectors.chatlog import sidecar as wx


class TestTheFourDiseasesAreFourSentences:
    """四种缺法要落进四个码。

    现场全部注入：这台微信装好了、密钥也在 handoff 里，真读数的话四种病一种都造不出来
    （10-25 第一版就是这么红的——它把"干净环境"当成"什么都没有"，而产物与密钥都从盘上读）。
    """

    def _diagnose(self, monkeypatch, *, binary: bool, go: bool, key: bool, healthy: bool = False) -> str:
        # "有产物"要指一个**真在盘上的文件**（这一档自己就行）：分类器判的是 `is_file()`，
        # 给一个不存在的路径会被它正确地当成"没产物"，那测的就是另一件事了。
        monkeypatch.setattr(wx, "binary_candidates", lambda *a, **kw: [__file__] if binary else [])
        monkeypatch.setattr(wx, "data_key_present", lambda env: key)
        monkeypatch.setattr(wx.shutil, "which", lambda *a, **kw: r"D:\Runtimes\go\bin\go.exe" if go else None)

        return wx.diagnose(env={}, healthy=healthy)[0]

    def test_each_missing_step_gets_its_own_code(self, monkeypatch) -> None:
        assert self._diagnose(monkeypatch, binary=False, go=False, key=False) == wx.STATE_GO_MISSING
        assert self._diagnose(monkeypatch, binary=False, go=True, key=False) == wx.STATE_BUILD_MISSING
        assert self._diagnose(monkeypatch, binary=True, go=True, key=False) == wx.STATE_KEY_MISSING
        assert self._diagnose(monkeypatch, binary=True, go=True, key=True) == wx.STATE_NOT_LISTENING
        assert self._diagnose(monkeypatch, binary=True, go=True, key=True, healthy=True) == wx.STATE_READY

    def test_a_live_port_does_not_go_back_to_blaming_the_toolchain(self, monkeypatch) -> None:
        """5030 都在应答了还回头说"你 Go 没装"，那句"为什么采不到"就是假的。"""
        assert self._diagnose(monkeypatch, binary=False, go=False, key=False, healthy=True) == wx.STATE_READY

    def test_the_sentences_are_not_the_same_sentence(self) -> None:
        notes = {state: wx.note_for(state) for state in (
            wx.STATE_GO_MISSING, wx.STATE_BUILD_MISSING, wx.STATE_KEY_MISSING, wx.STATE_NOT_LISTENING)}

        assert len(set(notes.values())) == 4, "四种病并成一句话，用户就分不出该动哪一步"
        for note in notes.values():
            assert "：" in note or "。" in note, "那句话要带下一步，不是一个词"


class TestNothingOnScreenNamesADeveloperArtifact:
    @pytest.mark.parametrize("state", [
        wx.STATE_GO_MISSING, wx.STATE_BUILD_MISSING, wx.STATE_KEY_MISSING,
        wx.STATE_NOT_LISTENING, wx.STATE_READY,
    ])
    def test_notes_carry_no_file_env_or_command(self, state: str) -> None:
        note = wx.note_for(state)

        # 环境变量名（`PLOBI_CHATLOG_BIN` 这一族）、反引号里的命令、盘符路径、`.env`
        assert not re.search(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", note), note
        assert "`" not in note, note
        assert ".env" not in note, note
        assert not re.search(r"^[A-Za-z]:[\\/]", note), note

    def test_the_probe_hand_reports_the_code_and_the_sentence(self, monkeypatch) -> None:
        """探针那一只手：报前提、不报"采得到"，且 code 与句子一起给（界面按 code 查四语目录）。"""
        from plobi.collectors.chatlog import SPEC

        monkeypatch.setattr("plobi.collectors.sidecar.port_listening", lambda port, **kw: False)

        result = SPEC.probe()

        assert result["ready"] is False
        assert result["code"] in (wx.STATE_GO_MISSING, wx.STATE_BUILD_MISSING,
                                  wx.STATE_KEY_MISSING, wx.STATE_NOT_LISTENING)
        assert result["detail"]

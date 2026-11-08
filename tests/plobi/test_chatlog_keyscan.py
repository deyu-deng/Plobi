"""微信取钥那一族的测试（`WP-WECHAT-KEY-4X`）。

**全是合成的**：不读真微信进程、不碰真密钥、不写真配置。
真实那台上的验证是另一件事（`Docs/tasks/wechat-key-4x-task.md` 上记着）。

这一档要钉住的是**这次真坏过的那格**：锚点 RVA 随微信更新漂移（4.1.15.13 的
``0x9492618`` 在 4.1.15.56 上跑到 ``0x96789f8``）⇒ 锚点必须现找，不能写死。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from pathlib import Path

import pytest

from plobi.collectors.chatlog import keyscan
from plobi.collectors.chatlog import keyhand


# --------------------------------------------------------------------------- #
# 造一个最小的 PE（够跑段表解析那一刀）
# --------------------------------------------------------------------------- #

def _write_fake_pe(path: Path, sections: list[tuple[str, bytes]]) -> None:
    """``sections`` = [(节名, 原始字节)]。造出的 PE 有真实段表，够 :func:`_sections` 用。"""
    header = bytearray(0x1000)
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", header, 0x84 + 2, len(sections))     # NumberOfSections
    struct.pack_into("<H", header, 0x84 + 16, 0)                # SizeOfOptionalHeader
    table = 0x84 + 20
    raw_offset = 0x1000
    body = bytearray()
    for index, (name, blob) in enumerate(sections):
        entry = table + index * 40
        header[entry:entry + 8] = name.encode("ascii").ljust(8, b"\x00")
        struct.pack_into("<I", header, entry + 8, len(blob))     # VirtualSize
        struct.pack_into("<I", header, entry + 12, 0x1000 + index * 0x10000)  # VirtualAddress
        struct.pack_into("<I", header, entry + 16, len(blob))    # SizeOfRawData
        struct.pack_into("<I", header, entry + 20, raw_offset)   # PointerToRawData
        body += blob
        raw_offset += len(blob)
    path.write_bytes(bytes(header) + bytes(body))


def _xor_blob(parts: list[bytes], gap: int = 3) -> bytes:
    """社区那条特征：四段 ``mov rdx, imm64`` ＋ 一句 ``test rax,rax``。"""
    out = bytearray()
    for part in parts:
        out += b"\x48\xBA" + part
        out += b"\x90" * gap
    out += b"\x48\x85\xC0"
    return bytes(out)


# --------------------------------------------------------------------------- #
# 造一页真的 SQLCipher 4 首屏
# --------------------------------------------------------------------------- #

def _make_page1(passphrase: bytes, salt: bytes = b"\x01" * 16) -> bytes:
    """按 vendor ``decrypt/common/common.go`` 那一式造一页带正确 HMAC 的首屏。

    这么造出来的首屏，正好是**校验判据的对照物**：解得开的必须是真密钥。
    """
    assert len(page := bytearray(4096)) == 4096
    page[0:16] = salt
    enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, keyscan.PBKDF2_ROUNDS, 32)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, 32)
    data_end = 4096 - keyscan.RESERVE + keyscan.IV_SIZE
    mac = hmac.new(mac_key, bytes(page[16:data_end]), hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    page[data_end:data_end + 64] = mac.digest()
    return bytes(page)


# --------------------------------------------------------------------------- #

class TestAnchorIsFoundNotHardcoded:
    """锚点必须现找——写死的那个随微信更新漂一次就废一次。"""

    def test_anchor_rva_is_read_out_of_the_dll(self, tmp_path: Path) -> None:
        dll = tmp_path / "Weixin.dll"
        # .rdata 段里放一份 MMV1$，虚拟地址 0x1000 ⇒ RVA 应当是 0x1000 + 段内偏移
        _write_fake_pe(dll, [(".rdata", b"\x00" * 32 + b"MMV1$\x00\x00\x00" + b"\x00" * 32)])
        rva = keyscan.locate_anchor_rva(str(dll))
        assert rva == 0x1000 + 32

    def test_anchor_is_not_the_hardcoded_old_rva(self, tmp_path: Path) -> None:
        """4.1.15.13 那份常量是 0x9492618；4.1.15.56 上同一个常量换了位置。"""
        dll = tmp_path / "Weixin.dll"
        _write_fake_pe(dll, [(".rdata", b"MMV1$\x00\x00\x00" + b"\x00" * 64)])
        rva = keyscan.locate_anchor_rva(str(dll))
        assert rva == 0x1000
        assert rva != 0x9492618

    def test_no_anchor_means_none_not_a_guess(self, tmp_path: Path) -> None:
        dll = tmp_path / "Weixin.dll"
        _write_fake_pe(dll, [(".rdata", b"\x00" * 128)])
        assert keyscan.locate_anchor_rva(str(dll)) is None

    def test_text_section_is_the_fallback(self, tmp_path: Path) -> None:
        dll = tmp_path / "Weixin.dll"
        _write_fake_pe(dll, [(".text", b"MMV1$\x00\x00\x00")])
        assert keyscan.locate_anchor_rva(str(dll)) == 0x1000


class TestXorConstants:
    def test_constants_are_read_out_of_text(self, tmp_path: Path) -> None:
        parts = [bytes([i]) * 8 for i in range(4)]
        dll = tmp_path / "Weixin.dll"
        _write_fake_pe(dll, [(".text", _xor_blob(parts))])
        found = keyscan.xor_constants(str(dll))
        assert found == [b"".join(parts)]

    def test_a_dll_without_the_pattern_yields_nothing(self, tmp_path: Path) -> None:
        dll = tmp_path / "Weixin.dll"
        _write_fake_pe(dll, [(".text", b"\x90" * 256)])
        assert keyscan.xor_constants(str(dll)) == []


class TestValidation:
    def test_the_right_key_validates(self) -> None:
        key = bytes(range(32))
        assert keyscan.validate_candidate(key, _make_page1(key)) is True

    def test_a_wrong_key_does_not(self) -> None:
        page = _make_page1(bytes(range(32)))
        assert keyscan.validate_candidate(bytes([9]) * 32, page) is False

    def test_the_derived_form_validates_too(self) -> None:
        """派生结果（enc_key）那一形态也要认——微信有时在内存里放的是它。"""
        key = bytes(range(32))
        page = _make_page1(key)
        enc = hashlib.pbkdf2_hmac("sha512", key, page[:16], keyscan.PBKDF2_ROUNDS, 32)
        assert keyscan.validate_candidate(enc, page) is True

    def test_a_truncated_page_never_validates(self) -> None:
        key = bytes(range(32))
        assert keyscan.validate_candidate(key, _make_page1(key)[:100]) is False


class TestEntropyFilter:
    def test_all_zero_bytes_are_not_a_key(self) -> None:
        assert keyscan._looks_random(b"\x00" * 32) is False

    def test_plain_text_is_not_a_key(self) -> None:
        assert keyscan._looks_random(b"this is not a secret at all!!"[:32].ljust(32, b" ")) is False

    def test_random_bytes_are_a_key(self) -> None:
        assert keyscan._looks_random(bytes(range(32))) is True


class TestAccountDir:
    def test_account_dir_drops_the_last_three_parts(self) -> None:
        path = r"D:\Data\Local\Weixin\xwechat_files\wxid_abc_c19\db_storage\message\message_0.db"
        assert keyscan._account_dir(path) == \
            r"D:\Data\Local\Weixin\xwechat_files\wxid_abc_c19"


class TestHandoff:
    def test_key_is_written_and_merged(self, tmp_path: Path) -> None:
        keyscan.write_handoff(tmp_path, b"\x01" * 32, {"data_dir": r"D:\x", "version": 4})
        import json
        written = json.loads((tmp_path / "chatlog.json").read_text(encoding="utf-8"))
        assert written["data_key"] == ("01" * 32)
        assert written["data_dir"] == r"D:\x"
        assert written["version"] == 4

    def test_existing_keys_survive(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "chatlog.json").write_text(
            json.dumps({"data_key": "aa" * 32, "img_key": "bb" * 16}), encoding="utf-8")
        keyscan.write_handoff(tmp_path, b"\x02" * 32, {"version": 4})
        written = json.loads((tmp_path / "chatlog.json").read_text(encoding="utf-8"))
        assert written["data_key"] == ("02" * 32)
        assert written["img_key"] == "bb" * 16

    def test_set_key_accepts_hex_and_refuses_garbage(self, tmp_path: Path) -> None:
        keyscan.set_key(tmp_path, "0A" * 32)
        import json
        written = json.loads((tmp_path / "chatlog.json").read_text(encoding="utf-8"))
        assert written["data_key"] == "0a" * 32
        with pytest.raises(ValueError):
            keyscan.set_key(tmp_path, "zz")
        with pytest.raises(ValueError):
            keyscan.set_key(tmp_path, "ab")


class TestFetchShortCircuits:
    def test_a_key_already_in_hand_means_no_touching_wechat(self, tmp_path: Path) -> None:
        import json
        (tmp_path / "chatlog.json").write_text(json.dumps({"data_key": "ab" * 32}), encoding="utf-8")
        ok, note = keyscan.fetch({"CHATLOG_DIR": str(tmp_path)})
        assert ok is True
        assert "没去动微信" in note


class TestPrepareTriesOursFirst:
    """接入点：新版微信那条先试，上游那条兜底；两路都不成时说我们那条的真话。"""

    def test_ours_wins_and_the_upstream_is_never_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plobi.collectors import chatlog

        calls: list[str] = []
        monkeypatch.setattr(keyscan, "fetch", lambda *a, **k: (calls.append("ours"), (True, "ours"))[1])
        monkeypatch.setattr(keyhand, "fetch", lambda *a, **k: (calls.append("upstream"), (True, "u"))[1])
        ok, note = chatlog._prepare()
        assert ok is True and note == "ours"
        assert calls == ["ours"]

    def test_upstream_is_the_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plobi.collectors import chatlog

        monkeypatch.setattr(keyscan, "fetch", lambda *a, **k: (False, "ours 说真话"))
        monkeypatch.setattr(keyhand, "fetch", lambda *a, **k: (True, "upstream"))
        ok, note = chatlog._prepare()
        assert ok is True and note == "upstream"

    def test_when_both_fail_the_truthful_reason_is_the_ours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plobi.collectors import chatlog

        monkeypatch.setattr(keyscan, "fetch", lambda *a, **k: (False, "我们那条说真话"))
        monkeypatch.setattr(keyhand, "fetch", lambda *a, **k: (False, "上游那句"))
        monkeypatch.setattr(keyscan, "routed_away", lambda: "")
        ok, note = chatlog._prepare()
        assert ok is False
        assert note == "我们那条说真话"


class TestVersionRouting:
    """这条路只服务微信 4.x；3.x 走另一条，不许拿「没在读库」那种话去回敬它。"""

    @pytest.mark.skipif(os.name != "nt", reason="只读扫描是 Windows 那一条")
    def test_old_wechat_goes_to_the_other_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyscan, "_wechat_process", lambda: (1234, r"C:\x\WeChat.exe"))
        monkeypatch.setattr(keyscan, "_file_version", lambda path: "3.9.12.51")
        key, note, _extra = keyscan.extract()
        assert key is None
        assert "另一条路" in note
        assert "3.9.12.51" in note

    @pytest.mark.skipif(os.name != "nt", reason="只读扫描是 Windows 那一条")
    def test_four_x_is_not_routed_away(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyscan, "_wechat_process", lambda: (1234, r"C:\x\Weixin.exe"))
        monkeypatch.setattr(keyscan, "_file_version", lambda path: "4.1.15.56")
        monkeypatch.setattr(keyscan, "_open_databases", lambda pid: [])
        key, note, _extra = keyscan.extract()
        assert key is None
        assert "另一条路" not in note
        assert "没在读本机的聊天库" in note

    @pytest.mark.skipif(os.name != "nt", reason="只读扫描是 Windows 那一条")
    def test_routed_away_names_the_old_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyscan, "_wechat_process", lambda: (1234, r"C:\x\WeChat.exe"))
        monkeypatch.setattr(keyscan, "_file_version", lambda path: "3.9.12.51")
        assert keyscan.routed_away() == "3.9.12.51"

    @pytest.mark.skipif(os.name != "nt", reason="只读扫描是 Windows 那一条")
    def test_routed_away_is_empty_on_four_x(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(keyscan, "_wechat_process", lambda: (1234, r"C:\x\Weixin.exe"))
        monkeypatch.setattr(keyscan, "_file_version", lambda path: "4.1.15.56")
        assert keyscan.routed_away() == ""


class TestPrepareWhenBothRoutesFail:
    """两路都不成时，屏幕上那句话得是**那一版微信真正管用的那条路**的真话。"""

    def test_old_wechat_says_the_upstream_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plobi.collectors import chatlog

        monkeypatch.setattr(keyscan, "fetch", lambda *a, **k: (False, "这一版微信（3.9.12.51）的密钥由另一条路取。"))
        monkeypatch.setattr(keyhand, "fetch", lambda *a, **k: (False, "上游那句：它没找到密钥"))
        monkeypatch.setattr(keyscan, "routed_away", lambda: "3.9.12.51")
        ok, note = chatlog._prepare()
        assert ok is False
        assert note == "上游那句：它没找到密钥"

    def test_four_x_says_our_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plobi.collectors import chatlog

        monkeypatch.setattr(keyscan, "fetch", lambda *a, **k: (False, "我们那条说真话"))
        monkeypatch.setattr(keyhand, "fetch", lambda *a, **k: (False, "上游那句"))
        monkeypatch.setattr(keyscan, "routed_away", lambda: "")
        ok, note = chatlog._prepare()
        assert ok is False
        assert note == "我们那条说真话"


class TestUpstreamCopyNoLongerBlamesTheWrongThing:
    def test_keyhand_does_not_say_wechat_must_be_logged_in(self) -> None:
        """旧那句「它要正在登录着的微信」把原因说错了（真因是微信版本）。"""
        source = Path(keyhand.__file__).read_text(encoding="utf-8")
        assert "它要正在登录着的微信" not in source

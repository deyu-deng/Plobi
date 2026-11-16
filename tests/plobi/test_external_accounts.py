"""多账号那一层（11-16）：**读清单 / 选身份 / 派活时交凭据**。

这一族（CodeBuddy／WorkBuddy）盘上的事实（11-16 现读）：

* 这一族下面有**两个身份**（两个不同的 uid），来自**三个产品各存各的**身份文件；
* 厂商把其中两个的昵称**加密**了 ⇒ 读不出名字（读得出的是标识的前八位）；
* 厂商自己的账号切换走**交互式**那条路（`/login`），非交互的派活腿吃不下
  ⇒ 派活时走厂商给非交互进程留的那个入口：**凭据快照文件**。

**没在盘上实测过的，一律不写成事实**：这一档的判据全是**造出来的假身份文件**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import external_accounts as ea  # noqa: E402

UID_A = "2fface12-fe18-4517-a6f7-48009aa628c8"
UID_B = "8fabe04c-3f5f-4851-b9d6-43d3b63256d2"
#: 厂商把昵称加密时的那份形状（读不出名字 ⇒ 退到标识前八位）。
ENCRYPTED = {"$wbEncrypted": 1, "envelope": "eyJzdWl0ZSI6MSw="}


def _write_identity(root: Path, name: str, uid: str, nickname, *, current: bool = True,
                   token: str = "tok") -> None:
    """造一份**形状与盘上逐字同形**的身份文件。"""
    folder = root / "CodeBuddyExtension" / "Data" / "Public" / "auth"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "account": {"uid": uid, "nickname": nickname, "type": "personal", "lastLogin": current},
        "auth": {
            "accessToken": f"{token}-access",
            "refreshToken": f"{token}-refresh",
            "tokenType": "Bearer",
            "expiresAt": 1797070981517,
        },
        "accounts": [{"uid": uid, "nickname": nickname, "type": "personal", "lastLogin": current}],
        "allAccounts": [{"uid": uid, "nickname": nickname, "type": "personal", "lastLogin": current}],
    }
    (folder / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def fake_localappdata(tmp_path, monkeypatch):
    """把 ``LOCALAPPDATA`` 指到临时目录 ⇒ 那一族的三个身份文件都在那儿。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    ea.forget_accounts_cache()
    yield tmp_path
    ea.forget_accounts_cache()


# ── 读清单 ──────────────────────────────────────────────────────────────

def test_the_two_identities_are_read_and_the_encrypted_name_falls_back_to_the_id(fake_localappdata):
    """两个身份都在；**厂商把昵称加密的那一个 ⇒ 退到标识前八位**（不编名字、不给空白）。"""
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")
    _write_identity(fake_localappdata, "workbuddy-desktop-ai.info", UID_B, ENCRYPTED)

    accounts = ea.read_accounts("codebuddy")

    by_id = {row["id"]: row for row in accounts}
    assert set(by_id) == {UID_A, UID_B}
    assert by_id[UID_A]["label"] == "190****0997"
    assert by_id[UID_B]["label"] == UID_B[:8]
    assert by_id[UID_B]["label"] != ""


def test_the_same_identity_in_two_files_is_merged_into_one_row(fake_localappdata):
    """同一个 uid 出现在几个文件里 ⇒ **一条**（不是两行同一个账号）。"""
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")
    _write_identity(fake_localappdata, "workbuddy-desktop.info", UID_A, ENCRYPTED)

    accounts = ea.read_accounts("codebuddy")

    assert len(accounts) == 1
    # 显示名取**第一个读到的**：加密那份读不出名字，先读到的明文那份就赢了。
    assert accounts[0]["label"] == "190****0997"


def test_only_the_first_products_own_login_marks_this_family_current(fake_localappdata):
    """**文件次序 = 产品次序**：第一份文件里当前登录的那个，才是这一族当前的身份。

    （这一族的三个身份文件分属三个产品，各登录着各自的那个 ⇒ 两个都标「当前」是假话。）
    """
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")
    _write_identity(fake_localappdata, "workbuddy-desktop-ai.info", UID_B, "190****0997")

    by_id = {row["id"]: row for row in ea.read_accounts("codebuddy")}

    assert by_id[UID_A]["current"] is True
    assert by_id[UID_B]["current"] is False


def test_a_family_without_the_rule_has_no_accounts():
    """没登记规矩的那一族 ⇒ **空清单**（"没做多账号" ≠ "它没有账号"）。"""
    assert ea.read_accounts("qoder") == []


# ── 凭据 ────────────────────────────────────────────────────────────────

def test_the_credential_comes_from_the_file_whose_own_account_is_the_chosen_one(fake_localappdata):
    """**凭据从哪个文件取**：那个文件里「当前身份」正好是你选的那个的那个文件。

    （同一族里几个产品各存各的令牌 ⇒ 取错了就是拿另一个产品的令牌去打这一家的后端。）
    """
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997",
                    token="tok-a")
    _write_identity(fake_localappdata, "workbuddy-desktop-ai.info", UID_B, ENCRYPTED, token="tok-b")

    session = ea.account_session("codebuddy", UID_B)

    assert session["account"]["uid"] == UID_B
    assert session["auth"]["accessToken"] == "tok-b-access"


def test_an_identity_that_is_not_on_disk_has_no_credential(fake_localappdata):
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")

    assert ea.account_session("codebuddy", UID_B) == {}


# ── 选身份 ──────────────────────────────────────────────────────────────

def test_the_chosen_identity_round_trips_through_the_config(fake_localappdata):
    """主人选的那个落在 ``config.yaml`` 的 ``delegation.offload_accounts``。"""
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")
    _write_identity(fake_localappdata, "workbuddy-desktop-ai.info", UID_B, ENCRYPTED)

    ea.set_selected_account("codebuddy", UID_B)

    assert ea.selected_account("codebuddy") == UID_B
    # 选一个不存在的身份 ⇒ 拒绝（不许把一个没有的身份记进去）。
    with pytest.raises(ValueError):
        ea.set_selected_account("codebuddy", "没有这个身份")


# ── 派活时：把它交出去 ──────────────────────────────────────────────────

def test_the_snapshot_is_the_shape_the_vendor_reads(fake_localappdata):
    """厂商读的那份形状：``{"session": {"account": …, "auth": …}}``（现读厂商包）。

    **里面那两块是盘上原样搬过来的**，不拼、不造、不补。
    """
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")
    ea.set_selected_account("codebuddy", UID_A)

    env: dict[str, str] = {}
    account_id, snapshot = ea.apply_account_env("codebuddy", env)

    assert account_id == UID_A
    assert snapshot is not None
    assert "CODEBUDDY_AUTH_SNAPSHOT_PATH" in env
    assert env["CODEBUDDY_AUTH_SNAPSHOT_PATH"] == str(snapshot)

    payload = json.loads(Path(env["CODEBUDDY_AUTH_SNAPSHOT_PATH"]).read_text(encoding="utf-8"))
    assert set(payload) == {"session"}
    assert payload["session"]["account"]["uid"] == UID_A
    assert payload["session"]["auth"]["accessToken"] == "tok-access"


def test_nothing_is_handed_over_when_no_identity_is_chosen(fake_localappdata):
    """**没选身份 ⇒ 什么都不交**：厂商用它自己当前登录的那个，行为一个字不变。"""
    _write_identity(fake_localappdata, "Tencent-Cloud.coding-copilot.info", UID_A, "190****0997")

    env: dict[str, str] = {}
    account_id, snapshot = ea.apply_account_env("codebuddy", env)

    assert account_id == ""
    assert snapshot is None
    assert env == {}


def test_a_family_without_a_credential_handover_gets_nothing(fake_localappdata):
    """没登记凭据交接入口的那一族 ⇒ 什么也不交（不猜一个入口）。"""
    env: dict[str, str] = {}

    assert ea.apply_account_env("qoder", env) == ("", None)
    assert env == {}

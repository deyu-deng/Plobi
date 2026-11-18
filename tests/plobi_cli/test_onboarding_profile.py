"""引导末步的答案 ⇒ **两份文件**（WP-ONBOARDING-WELCOME-FLOW）。

主人 11-18 13:5x：「关于用户的是落在 USERS 里面，关于 Agent 自己的性格和姓名应该是落在
SOUL 里面呀。这两个文件都是可以以后在对话中改动的呀。」

钉四条：

* 关于**用户的**（画像两问 ＋ 期望的称呼）落 `memory/USER.md`（记忆层 user scope）；
* 关于**Agent 自己**的（名字 ＋ 性格）落**顶层** `SOUL.md`，且写在两段围栏**之外**；
* 没答的那几项**不写**（不替他编）；
* 重复引导**不叠第二份**（USER.md 不重复条目，SOUL.md 那一格整段替换）。
"""

import io
import os
from pathlib import Path

import pytest

from plobi_cli.onboarding_profile import (
    SOUL_BLOCK_END,
    SOUL_BLOCK_START,
    soul_path,
    user_profile_path,
    write_onboarding_profile,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把家指到一个临时目录（家与共享根同一套覆盖链 ⇒ 两份文件都在那儿）。"""
    fake = str(tmp_path / "home")
    os.makedirs(fake, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", fake)
    return fake


def _seed_soul(home):
    """一份带围栏的顶层 SOUL.md（照真实那份的形状：身份围栏 ＋ 规矩围栏）。"""
    path = Path(home) / "SOUL.md"
    path.write_text(
        "You are Plobi Agent.\n"
        "\n"
        ":::PLOBI_CONDUCTOR_ASK_ROUTING:::\n"
        "## 身份（硬规则）\n"
        "\n"
        "- 你是本机 Plobi 指挥。\n"
        ":::PLOBI_CONDUCTOR_ASK_END:::\n"
        "\n"
        ":::PLOBI_L1_ASK_ROUTING:::\n"
        "## 别的规矩\n"
        ":::PLOBI_L1_ASK_END:::\n",
        encoding="utf-8",
    )
    return path


def test_关于用户的落_USER_md(home):
    """画像两问 ＋ 期望的称呼 ⇒ `memory/USER.md`（记忆层 user scope）。"""
    result = write_onboarding_profile(
        {"purpose": "写论文与查资料", "domain": "理论物理", "user_title": "邓老师"}
    )

    assert result["ok"] is True
    path = user_profile_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "写论文与查资料" in text
    assert "理论物理" in text
    assert "邓老师" in text
    # 只答了用户那几项 ⇒ SOUL.md 那一格一个字都不该动。
    assert not soul_path().exists()


def test_关于Agent自己的落顶层_SOUL_md(home):
    """名字 ＋ 性格 ⇒ 顶层 `SOUL.md`，且在两段围栏**之外**。"""
    seeded = _seed_soul(home)

    result = write_onboarding_profile({"agent_name": "小满", "agent_personality": "说话简短，先给结论"})

    assert result["soul_updated"] is True
    after = seeded.read_text(encoding="utf-8")
    # 围栏里的东西一个字没动。
    assert "## 身份（硬规则）" in after
    assert "你是本机 Plobi 指挥。" in after
    assert after.startswith("You are Plobi Agent.")
    # 新那一格在最后，且带标记。
    assert after.index(SOUL_BLOCK_START) > after.index(":::PLOBI_L1_ASK_END:::")
    assert "小满" in after
    assert "说话简短，先给结论" in after
    assert after.rstrip().endswith(SOUL_BLOCK_END)


def test_没答的那几项不写(home):
    """空载荷 ⇒ 两份文件都不该多出一个字节（不替他编）。"""
    result = write_onboarding_profile({})

    assert result["ok"] is True
    assert result["user_entries_added"] == []
    assert result["soul_updated"] is False
    assert not user_profile_path().exists()
    assert not soul_path().exists()


def test_重复引导不叠第二份(home):
    """第二次引导 ⇒ USER.md 不重复条目，SOUL.md 那一格整段替换。"""
    _seed_soul(home)
    payload = {
        "purpose": "写论文",
        "domain": "理论物理",
        "user_title": "邓老师",
        "agent_name": "小满",
        "agent_personality": "说话简短",
    }

    write_onboarding_profile(payload)
    user_first = user_profile_path().read_text(encoding="utf-8")
    soul_first = soul_path().read_text(encoding="utf-8")

    write_onboarding_profile(payload)
    assert user_profile_path().read_text(encoding="utf-8") == user_first
    assert soul_path().read_text(encoding="utf-8") == soul_first
    assert soul_first.count(SOUL_BLOCK_START) == 1


def test_USER_md里已有的条目留着(home):
    """引导不许把用户自己写过的那份画像抹掉。"""
    path = user_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("用户偏好：说话用中文。\n", encoding="utf-8")

    write_onboarding_profile({"purpose": "写论文"})

    text = path.read_text(encoding="utf-8")
    assert "用户偏好：说话用中文。" in text
    assert "写论文" in text

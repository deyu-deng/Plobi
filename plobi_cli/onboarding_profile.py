"""引导末步的答案 ⇒ **两份文件**（WP-ONBOARDING-WELCOME-FLOW）。

主人 11-18 13:5x：「关于用户的是落在 USERS 里面，关于 Agent 自己的性格和姓名应该是落在
SOUL 里面呀。这两个文件都是可以以后在对话中改动的呀。我感觉你没有搞懂我们的架构设计」

⇒ 这一档只做两件事，**不另造第三份存储**：

* **关于用户的**（画像两问 ＋ 用户期望的称呼）⇒ `memory/USER.md`
  （记忆层的 **user** scope：`tools.memory_tool.memory_path("user")`；条目用 `§` 分隔，
  与记忆工具同一形状 ⇒ 以后在对话里用记忆工具就能改）；
* **关于 Agent 自己的**（名字 ＋ 性格／口吻）⇒ **顶层 `SOUL.md`**
  （指挥那份身份；写在两段围栏**之外** —— 与各 L2 行那份 soul 同一口径：「上面那些是你的
  **分工**，不是你这个人。你说话的样子、你的脾气写在这份文件围栏之外」）。

两份都是**普通文件** ⇒ 以后在对话里说一声就能改。

写盘一律「临时文件 → flush+fsync → `os.replace`」（`utils.atomic_replace` 那一套）。
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

#: 顶层 SOUL.md 里那段「你这个人」的标记（重复引导时整段替换，不叠第二份）。
SOUL_BLOCK_START = "<!-- plobi:onboarding-personality -->"
SOUL_BLOCK_END = "<!-- /plobi:onboarding-personality -->"


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写一个文本文件（临时文件 → fsync → replace）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            from utils import atomic_replace

            atomic_replace(tmp_path, path)
        except Exception:  # noqa: BLE001 — 退回到普通的 replace，也要是原子的
            os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            os.unlink(str(tmp_path))
        except OSError:
            pass
        raise


def _split_entries(text: str) -> List[str]:
    """`§` 分隔的条目（与 `plobi/mind/memory_api.py` 同一形状：一行只有一个 `§` 才是分隔符）。"""
    blocks: List[str] = []
    current: List[str] = []
    for line in (text or "").splitlines():
        if line.strip() == "§":
            blocks.append("\n".join(current).strip())
            current = []
        else:
            current.append(line)
    blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def _join_entries(entries: List[str]) -> str:
    return "\n§\n".join(e.strip() for e in entries if e.strip()) + ("\n" if entries else "")


def user_profile_path() -> Path:
    """用户的画像那一份（记忆层的 user scope ⇒ `memory/USER.md`）。"""
    from tools.memory_tool import memory_path

    return memory_path("user")


def soul_path() -> Path:
    """顶层 SOUL.md（指挥那份身份）。"""
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "SOUL.md"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def write_user_profile(entries: List[str]) -> List[str]:
    """把**关于用户的**那几条写进 `USER.md`（已有的留着，同样的不写第二遍）。

    返回真正新增的条目。
    """
    wanted = [e for e in (_clean(x) for x in entries) if e]
    if not wanted:
        return []

    path = user_profile_path()
    current = _split_entries(path.read_text(encoding="utf-8")) if path.is_file() else []
    fresh: List[str] = []
    for entry in wanted:
        if entry in current:
            continue
        current.append(entry)
        fresh.append(entry)

    if fresh:
        _atomic_write_text(path, _join_entries(current))
    return fresh


def write_soul_personality(name: str, personality: str) -> bool:
    """把**名字 ＋ 性格**写进顶层 `SOUL.md` 围栏外那一格（整段替换，不叠第二份）。

    返回有没有改动。
    """
    name = _clean(name)
    personality = _clean(personality)
    if not name and not personality:
        return False

    path = soul_path()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""

    lines = [
        SOUL_BLOCK_START,
        "## 你是谁（主人在第一次安装时给的）",
        "",
    ]
    if name:
        lines.append(f"- 你的名字：**{name}**。用户这么叫你，你就这么自称。")
    if personality:
        lines.append(f"- 你的性格与说话的样子：{personality}")
    lines.extend(["", SOUL_BLOCK_END])
    block = "\n".join(lines)

    if SOUL_BLOCK_START in text and SOUL_BLOCK_END in text:
        start = text.index(SOUL_BLOCK_START)
        end = text.index(SOUL_BLOCK_END) + len(SOUL_BLOCK_END)
        updated = text[:start] + block + text[end:]
    else:
        updated = (text.rstrip() + "\n\n" + block + "\n") if text.strip() else (block + "\n")

    if updated == text:
        return False
    _atomic_write_text(path, updated)
    return True


def write_onboarding_profile(payload: Dict[str, Any]) -> Dict[str, Any]:
    """引导末步的答案 ⇒ 两份文件。

    `payload`：`purpose`（主要用途）、`domain`（工作领域）、`user_title`（用户期望的称呼）、
    `agent_name`（给指挥取的名字）、`agent_personality`（它的性格／口吻）。

    空的那几项不写（用户没答就是没答，不替他编）。
    """
    purpose = _clean(payload.get("purpose"))
    domain = _clean(payload.get("domain"))
    user_title = _clean(payload.get("user_title"))
    agent_name = _clean(payload.get("agent_name"))
    agent_personality = _clean(payload.get("agent_personality"))

    user_entries: List[str] = []
    if purpose:
        user_entries.append(f"用户主要拿 Plobi 做：{purpose}")
    if domain:
        user_entries.append(f"用户的工作领域：{domain}")
    if user_title:
        user_entries.append(f"用户希望被称呼为：{user_title}")

    added = write_user_profile(user_entries)
    soul_changed = write_soul_personality(agent_name, agent_personality)

    return {
        "ok": True,
        "user_entries_added": added,
        "soul_updated": soul_changed,
        "user_path": str(user_profile_path()),
        "soul_path": str(soul_path()),
    }

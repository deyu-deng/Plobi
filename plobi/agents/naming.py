"""命名辅助：中文名 → 英文名 → ``agent_id``。

2026-11-16（i18n 定名刀）定下的两层：

* **显示名**：``display_name``（中文）+ ``display_name_en``（英文），缺英文回退中文；
* **门牌**：``agent_id``，ASCII、稳定、由**英文名**规范化而来，落盘目录名就用它。

所以新建 Agent 时用户只填一个中文名，英文名由这里翻出来预填（用户可改），
最后 ``agent_id`` 由英文名规范化 —— 这条链路里没有任何"名字的哈希"，
门牌看得懂、改得动。

翻译失败**不是错误**：返回空串，让前端那个框留空等用户自己填。
永不让"翻译不通"变成"建不出 Agent"。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# 只翻一个短名，不写文章：8 秒够一次小模型往返，超时就让用户手填。
DEFAULT_TIMEOUT = 8

# 英文名 = 1~4 个词，每词首字母大写；只许字母数字与单空格。
_MAX_WORDS = 4
_MAX_LEN = 48

_SYSTEM_PROMPT = (
    "You turn a person's product/agent name into a short English name.\n"
    "Rules:\n"
    "- Output ONLY the English name, no quotes, no explanation, no punctuation.\n"
    "- Title Case, 1 to 3 words, each word at most 12 characters.\n"
    "- It must be a meaningful English rendering, not pinyin, not a placeholder.\n"
    "- If the input is already English, return it in Title Case.\n"
    "- Example input: 普洛比 → output: Plobi\n"
    "- Example input: 大学物理（甲） → output: College Physics A\n"
    "- Example input: 日程秘书 → output: Agenda Secretary"
)


def _strip_noise(text: str) -> str:
    """去掉模型爱加的前后缀（引号、代码块、``英文名:`` 这类标签）。"""
    s = (text or "").strip()
    # ```...``` 围栏
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    # 第一行才是名字（模型偶尔会补一句解释）
    s = s.split("\n", 1)[0].strip()
    # 常见的"English name:"前缀
    s = re.sub(r"^(?:english\s*name|name|translation)\s*[:：]\s*", "", s, flags=re.IGNORECASE)
    # 引号 / 书名号 / 句号
    s = s.strip(" \t\"'“”‘’《》「」`。.")
    return s


# --------------------------------------------------------------------------- #
# 本地词表兜底
# --------------------------------------------------------------------------- #
#
# 实测（2026-11-16）：辅助 LLM 可能整条不可用（额度/未认证都会让
# ``english_name_for`` 回空）。那种情况下"自动预填"就是一句空话 —— 用户看到的
# 永远是空框加一句"你自己填"。所以先过一遍本地词表：它零延迟、不联网、可预期，
# 覆盖绝大多数 Agent 起名（秘书／课程／项目／研究……），LLM 只当第二级增强。
#
# 词表规则的硬约束：**整串都必须认得**（或剩下的是标点/数字）才用词表结果。
# 认不全就返回空——宁可让用户自己填，也不许把"大学物理（甲）"洗成"College"，
# 那是个看不出丢了什么的残名。
_GLOSSARY: dict[str, str] = {
    # 角色
    "日程秘书": "Agenda Secretary",
    "秘书": "Secretary",
    "助理": "Assistant",
    "管家": "Butler",
    "顾问": "Advisor",
    "教练": "Coach",
    "老师": "Teacher",
    "专家": "Expert",
    "助手": "Helper",
    "分身": "Twin",
    # 领域
    "日程": "Agenda",
    "课程": "Course",
    "项目": "Project",
    "研究": "Research",
    "科研": "Research",
    "学术": "Academic",
    "论文": "Paper",
    "实验": "Lab",
    "代码": "Code",
    "编程": "Coding",
    "写作": "Writing",
    "翻译": "Translate",
    "财务": "Finance",
    "投资": "Invest",
    "健康": "Health",
    "健身": "Fitness",
    "旅行": "Travel",
    "阅读": "Reading",
    "笔记": "Notes",
    "音乐": "Music",
    "设计": "Design",
    "产品": "Product",
    "运营": "Ops",
    "市场": "Market",
    "客服": "Support",
    "数据": "Data",
    "分析": "Analysis",
    "系统": "System",
    "能源": "Energy",
    "车辆": "Vehicle",
    "机械": "Mechanical",
    "热管理": "Thermal",
    "仿真": "Simulation",
    "建模": "Modeling",
    "学习": "Study",
    "工作": "Work",
    "生活": "Life",
    "日常": "Daily",
    # 学科
    "大学物理": "College Physics",
    "物理": "Physics",
    "数学": "Math",
    "化学": "Chemistry",
    "英语": "English",
    "语文": "Chinese",
    "历史": "History",
    "哲学": "Philosophy",
    "经济学": "Economics",
    "计算机": "Computer",
    "甲": "A",
    "乙": "B",
    "丙": "C",
    "上": "I",
    "下": "II",
}

# 名字里允许原样保留、不算"没认出来"的字符：标点、空白、数字、罗马数字。
_GLOSSARY_FILLER = " \t·・-—_()（）[]【】《》「」.。,，、:：!！?？0123456789IVXivx"


def glossary_english_name(text: str) -> str:
    """用本地词表把中文名拼成英文名；认不全就回空串（不产残名）。

    最长匹配优先（先认「日程秘书」再认「日程」+「秘书」），拼完一轮再洗一遍，
    所以词表的输出与 LLM 的输出走同一条清洗路径。
    """
    s = (text or "").strip()
    if not s:
        return ""

    keys = sorted(_GLOSSARY, key=len, reverse=True)
    out: list[str] = []
    i = 0

    while i < len(s):
        hit = ""

        for key in keys:
            if s.startswith(key, i):
                hit = key
                break

        if hit:
            out.append(_GLOSSARY[hit])
            i += len(hit)
            continue

        ch = s[i]

        if ch in _GLOSSARY_FILLER:
            # 标点当分隔，交给后面的清洗统一折空格。
            out.append(" ")
            i += 1
            continue

        # 有一个字认不出来 ⇒ 整串不用词表。残名比空框更糟。
        return ""

    return clean_english_name(" ".join(part for part in out if part.strip()))


def english_name_for(
    name: str,
    *,
    timeout: Optional[int] = None,
    allow_llm: bool = True,
) -> str:
    """把中文（或任意语言）名字翻成一个英文短名；翻不出来回空串。

    绝不抛异常——翻译是**锦上添花**，它失败只该让那个框空着，不该让建册流程报错。
    """
    text = (name or "").strip()

    if not text:
        return ""

    # 纯 ASCII 的名字不用翻：'My Project' 本来就是英文名。
    if not re.search(r"[^\x00-\x7f]", text):
        return text

    # 第一级：本地词表。零延迟、不联网，覆盖绝大多数起名场景。
    local = glossary_english_name(text)
    if local:
        return local

    # 词表没认出整串就不再硬猜：``allow_llm=False`` 的调用（表单边打边填那种）
    # 到此为止，回空让用户自己填 —— 不能让一次取名卡在一个 8 秒的超时上。
    if not allow_llm:
        return ""

    try:
        from agent.auxiliary_client import (  # type: ignore
            get_auxiliary_extra_body,
            get_text_auxiliary_client,
        )
    except Exception as exc:  # pragma: no cover - 环境缺失时静默降级
        logger.debug("naming: auxiliary client import failed: %s", exc)
        return ""

    try:
        client, aux_model = get_text_auxiliary_client("agent_naming")
    except Exception as exc:  # pragma: no cover
        logger.debug("naming: get_text_auxiliary_client failed: %s", exc)
        return ""

    if client is None or not aux_model:
        return ""

    try:
        resp = client.chat.completions.create(
            model=aux_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            max_tokens=32,
            timeout=timeout or DEFAULT_TIMEOUT,
            extra_body=get_auxiliary_extra_body() or None,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.info("naming: translate failed for %r (%s)", text, exc)
        return ""

    return clean_english_name(_strip_noise(raw))


def clean_english_name(value: str) -> str:
    """把候选英文名洗成能显示的形状（Title Case、只留字母数字与单空格）。

    洗完还是空的（比如模型给了一串中文）就当没翻出来 —— 宁可留空让用户填，
    也不许把一个看不懂的串塞进那个框。
    """
    s = _strip_noise(value)
    if not s:
        return ""

    # 非 ASCII 一律不认：英文名框里必须真的是英文，否则门牌又会退化成哈希。
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    words = [w for w in s.split(" ") if w][:_MAX_WORDS]
    if not words:
        return ""

    out = " ".join(w[:1].upper() + w[1:] for w in words)
    return out[:_MAX_LEN].strip()


def agent_id_from_english(english_name: str) -> str:
    """英文名 → 门牌：小写、非字母数字折成 ``-``、去首尾与连续连字符。

    ``agent_id`` 就是英文件名的小写化，**不再编码 role**（role 在它自己的字段里），
    **也不再带 ``l2-`` 之类的职级前缀、不再拼短哈希**。
    """
    base = re.sub(r"[^a-z0-9]+", "-", (english_name or "").strip().lower())
    return base.strip("-")


def naming_for(
    name: str,
    *,
    timeout: Optional[int] = None,
    allow_llm: bool = False,
) -> dict:
    """一次给出前端那两个框要的东西：``englishName`` 与 ``agentId``。

    翻不出来时 ``englishName`` 是空串，``agentId`` 也是空串 —— 前端据此
    让用户自己填，而不是替他编一个。
    """
    english = english_name_for(name, timeout=timeout, allow_llm=allow_llm)

    return {"englishName": english, "agentId": agent_id_from_english(english)}

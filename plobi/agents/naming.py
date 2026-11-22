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

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# 翻译端点都是"别人的服务"，带个自己的 UA，别装成浏览器。
_UA = "Plobi/1.0 (+naming)"

# 只翻一个短名，不写文章：8 秒够一次翻译端点往返，超时就让用户手填。
DEFAULT_TIMEOUT = 8

# 英文名 = 1~4 个词，每词首字母大写；只许字母数字与单空格。
_MAX_WORDS = 4
_MAX_LEN = 48


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
# 翻译引擎：能翻就翻，翻不动就空着
# --------------------------------------------------------------------------- #
#
# 用户 2026-11-22 裁决：**自维护词表**（中文→英文那张 _GLOSSARY）删掉——那是我们
# 替用户决定「日程秘书」该配成哪个英文，猜错的名字直接落到门牌和磁盘目录上。
# 同日追问「Google 只是举例，有没有更好的引擎」，于是分两条通道重排：
#
# **输入时（force=False）——要快**，边打边翻，等不起大模型：
#   1. 纯 ASCII   → 原样返回（'My Project' 本来就是英文名）；
#   2. DeepL      → ``PLOBI_TRANSLATE_DEEPL_KEY``，机翻里最像人话的一档
#                   （免费额度每月 50 万字符），配了 key 才走；
#   3. Google     → translate.googleapis.com 免费端点（联网，走 HTTPS_PROXY）；
#   4. MyMemory   → 免费无 key 的公共翻译记忆库，Google 被掐时的兜底；
#   5. 本地引擎   → ``PLOBI_TRANSLATE_LOCAL_URL``，自建 LibreTranslate 之类；
#   6. LLM        → 辅助模型，兜底（3 秒超时，不让框干等）。
#
# **点刷新时（force=True）——要好**：用户点了框旁那枚刷新按钮，说明他嫌上一次
# 翻得不对，这时把唯一真懂语境的辅助模型顶到最前（日程秘书 ⇒ Agenda Secretary，
# 机翻只会给 Schedule Secretary），慢一点也值：
#   ASCII → LLM(8 秒) → DeepL → Google → MyMemory → 本地。
#
# 两条通道都失败 ⇒ 回空串，让用户自己填。后端不做缓存。
_LOCAL_TRANSLATE_URL_ENV = "PLOBI_TRANSLATE_LOCAL_URL"
_DEEPL_KEY_ENV = "PLOBI_TRANSLATE_DEEPL_KEY"
_DEEPL_TRANSLATE_URL = "https://api-free.deepl.com/v2/translate"
# 免费 key 一律以 ``:fx`` 结尾且只能打 api-free 域名；Pro（付费）key 不带后缀，
# 打 api-free 会被 403 顶回来。用户在设置页填了哪种 key 我们事先不知道，按后缀
# 挑域名，免得「明明填了 key 却一个名字都翻不出来」。
_DEEPL_TRANSLATE_URL_PRO = "https://api.deepl.com/v2/translate"
_GOOGLE_TRANSLATE_URL = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=auto&tl=en&dt=t&q={text}"
)
_FALLBACK_LLM_TIMEOUT = 3
_MYMEMORY_TRANSLATE_URL = (
    "https://api.mymemory.translated.net/get?q={text}&langpair=zh-CN|en-US"
)


def _is_ascii(text: str) -> bool:
    """整串都是 ASCII ⇒ 已经是英文名，不用翻。"""
    return not re.search(r"[^\x00-\x7f]", text)


def deepl_endpoint(key: str) -> str:
    """按 key 形态挑 DeepL 域名：``:fx`` 结尾是免费 key，其余按 Pro 处理。

    公开（非 ``_`` 前缀）是因为设置页的「测试」按钮要打**同一台**主机：
    探测和真翻译必须落在同一个域名上，否则会出现「测试通过、取名全空」。
    """
    return (
        _DEEPL_TRANSLATE_URL
        if key.strip().endswith(":fx")
        else _DEEPL_TRANSLATE_URL_PRO
    )


def translate_local(text: str, *, timeout: Optional[float] = None) -> str:
    """本地翻译引擎：``PLOBI_TRANSLATE_LOCAL_URL?q=<文本>&sl=auto&tl=en``。

    没配这个环境变量就直接回空（跳过，不报错）；配了就认纯文本与
    ``{"translated"|"translation"|"text"|"result": ...}`` 几种常见回包。
    """
    base = os.environ.get(_LOCAL_TRANSLATE_URL_ENV, "").strip()

    if not base:
        return ""

    sep = "&" if "?" in base else "?"
    # 两套参数名一起发：``sl``/``tl`` 是 Google 系免费端点的写法，自建的
    # LibreTranslate 认的是 ``source``/``target``。多带一组对方不认识的 query
    # 参数不会报错（FastAPI / Flask 都只取自己认识的），但能省掉用户
    # 「照文档填了 .../translate 却一个名字都翻不动」的来回。
    url = f"{base}{sep}" + urllib.parse.urlencode(
        {
            "q": text,
            "sl": "auto",
            "tl": "en",
            "source": "auto",
            "target": "en",
        }
    )
    budget = timeout or DEFAULT_TIMEOUT

    try:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=budget) as response:
            body = response.read().decode("utf-8", "replace").strip()
    except Exception as exc:
        logger.info("naming: local engine failed for %r: %s", text, exc)
        return ""

    return clean_english_name(local_body_text(body))


def local_body_text(body: str) -> str:
    """本地引擎回包里挖出**译文原文**（不做英文名清洗）。

    ``translate_local`` 拿它当名字，这里单独公开是给设置页的「测试」按钮用：
    测试要如实显示端点回了什么（``你好 → Hello``），套上英文名清洗规则反而
    会把用户看的东西截短。纯文本回包也认——自建端点经常直接回一行字。
    """
    try:
        payload = json.loads(body)
    except Exception:
        return body.strip()

    if isinstance(payload, dict):
        # "translatedText" first: that is what LibreTranslate actually returns,
        # and it was the one spelling missing here — a stock self-hosted
        # LibreTranslate endpoint answered 200 and every name still came back
        # empty because none of the other keys matched.
        for key in (
            "translatedText",
            "translated_text",
            "translated",
            "translation",
            "text",
            "result",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        return payload[0].strip()

    return ""


def translate_google(text: str, *, timeout: Optional[float] = None) -> str:
    """Google 免费端点；回包是 ``[[["译文","原文",...],...],...]`` 这种嵌套数组。"""
    url = _GOOGLE_TRANSLATE_URL.format(text=urllib.parse.quote(text))
    budget = timeout or DEFAULT_TIMEOUT

    try:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=budget) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        logger.info("naming: google engine failed for %r: %s", text, exc)
        return ""

    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        return ""

    pieces = [
        piece[0]
        for piece in payload[0]
        if isinstance(piece, list) and piece and isinstance(piece[0], str)
    ]

    return clean_english_name("".join(pieces))



def translate_deepl(text: str, *, timeout: Optional[float] = None) -> str:
    """DeepL 免费版接口：``PLOBI_TRANSLATE_DEEPL_KEY`` 没配就跳过。

    机翻里它最像人话（会挑词、会调语序），所以排在 Google 前面；免费账号
    每月 50 万字符，改个名字用不完。回包是 ``{"translations":[{"text":...}]}``。
    """
    key = os.environ.get(_DEEPL_KEY_ENV, "").strip()

    if not key:
        return ""

    payload = urllib.parse.urlencode(
        {"text": text, "target_lang": "EN-US", "source_lang": "ZH"}
    ).encode()

    try:
        request = urllib.request.Request(
            deepl_endpoint(key),
            data=payload,
            headers={
                "User-Agent": _UA,
                "Authorization": f"DeepL-Auth-Key {key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout or DEFAULT_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        logger.info("naming: deepl engine failed for %r: %s", text, exc)
        return ""

    items = body.get("translations") if isinstance(body, dict) else None

    if isinstance(items, list) and items and isinstance(items[0], dict):
        value = items[0].get("text")
        if isinstance(value, str) and value.strip():
            return clean_english_name(value)

    return ""


def translate_mymemory(text: str, *, timeout: Optional[float] = None) -> str:
    """MyMemory 公共接口：不认证、每日有匿名额度，回包 ``{"responseData":{"translatedText":...}}``。

    Google 那条被网络掐掉时的兜底——质量不如前两条，但至少不会让框空着。
    """
    url = _MYMEMORY_TRANSLATE_URL.format(text=urllib.parse.quote(text))

    try:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=timeout or DEFAULT_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        logger.info("naming: mymemory engine failed for %r: %s", text, exc)
        return ""

    data = body.get("responseData") if isinstance(body, dict) else None
    value = data.get("translatedText") if isinstance(data, dict) else None

    if not isinstance(value, str) or not value.strip():
        return ""

    # 它翻不动时会把原文原样回给我们，或者回一句 "PLEASE SELECT TWO DISTINCT LANGUAGES"。
    if value.strip().lower().startswith(("please ", "my memory")) or re.search(
        r"[\u4e00-\u9fff]", value
    ):
        return ""

    return clean_english_name(value)

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


def translate_llm(text: str, *, timeout: Optional[float] = None) -> str:
    """辅助 LLM 引擎：唯一真懂语境的一条路，但慢。

    两条通道给它的位置不同：输入时（force=False）只当兜底，机翻全灭了才问它，
    且超时压到 3 秒；点刷新时（force=True）它排第一，慢一点也等它。


    额度耗尽 / 未认证一律当「这条不通」处理：回空串、不抛错，绝不让翻译失败
    连坐成「建不出 Agent」。
    """
    try:
        from agent.auxiliary_client import get_auxiliary_extra_body, get_text_auxiliary_client
    except Exception as exc:  # pragma: no cover - 依赖缺失时静默降级
        logger.info("naming: auxiliary client import failed: %s", exc)
        return ""

    try:
        client, aux_model = get_text_auxiliary_client("agent_naming")
    except Exception as exc:
        logger.info("naming: get_text_auxiliary_client failed: %s", exc)
        return ""

    if client is None or not aux_model:
        return ""

    try:
        response = client.chat.completions.create(
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
        raw = response.choices[0].message.content or ""
    except Exception as exc:
        logger.info("naming: llm engine failed for %r: %s", text, exc)
        return ""

    return clean_english_name(raw)


def english_name_for(
    name: str,
    *,
    timeout: Optional[float] = None,
    force: bool = False,
) -> str:
    """名字 → 英文短名。按通道依次试，全不成回空串（绝不抛异常）。

    ``force=True``（用户点了刷新按钮）走「提质」通道：辅助 LLM 提到第一位；
    否则走「要快」通道：机翻优先，LLM 只兜底。后端无缓存，翻一次就是一次。
    """
    text = _strip_noise(name or "")

    if not text:
        return ""

    if _is_ascii(text):
        return clean_english_name(text) or text

    if force:
        # 用户嫌上次翻得不对：慢一点也要给个真懂语境的。LLM 给满 8 秒。
        translated = translate_llm(text, timeout=timeout or DEFAULT_TIMEOUT)

        if translated:
            return translated

        engines = (translate_deepl, translate_google, translate_mymemory, translate_local)
    else:
        # 边打边翻：机翻先给个即时初值，LLM 只当最后一道网，且只给 3 秒。
        translated = _first_engine(
            (translate_deepl, translate_google, translate_mymemory, translate_local),
            text,
            timeout=timeout,
        )

        if translated:
            return translated

        return translate_llm(text, timeout=min(timeout or _FALLBACK_LLM_TIMEOUT, _FALLBACK_LLM_TIMEOUT))

    return _first_engine(engines, text, timeout=timeout)


def _first_engine(engines: Sequence[object], text: str, *, timeout: Optional[float] = None) -> str:
    """按顺序问每个引擎，第一个给出非空结果的就用它。"""
    for engine in engines:
        translated = engine(text, timeout=timeout)

        if translated:
            return translated

    return ""

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
    force: bool = False,
) -> dict:
    """一次给出前端那两个框要的东西：``englishName`` 与 ``agentId``。

    翻不出来时 ``englishName`` 是空串，``agentId`` 也是空串 —— 前端据此
    让用户自己填，而不是替他编一个。
    """
    english = english_name_for(name, timeout=timeout, force=force)

    return {"englishName": english, "agentId": agent_id_from_english(english)}

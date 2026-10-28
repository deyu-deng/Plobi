"""邮件正文取哪一段：plain 优先，没有才把 HTML 啃成文本（`WP-EMAIL-HTML-BODY`）。

单独一档而不塞进 `collector.py`：这一只需要摆弄正则，混在 IMAP 那一手里会让两种"取内容"
的话互相踩。旧版 `_body_text` 只认 `text/plain`，multipart 里找不到就交回空串 ⇒
**HTML-only 那一整类邮件（订阅信、学校通知、账单、验证码）正文整封丢掉**，池里那一行只剩标题。

啃 HTML 只做三件事，且顺序是判据：整块先丢（`<script>` 里的代码不是正文，去掉标签留下它
反而是脏数据）→ 换行标记留下（不然整封挤成一行）→ 其余标签去掉并还原实体。
**不引解析器，也不假装读懂表格布局**——那是另一件事。

附件与内嵌件一律不进：这一档改的是"取哪一段正文"，不是"接不接媒体"，
所以能力位 `supports_media` 照原样留 `None`（＝没实测），既不冒充测过也不声明能力。
"""

from __future__ import annotations

import re
from email.message import Message
from html import unescape as html_unescape

#: 整块丢掉的两种标签（`.*?` 配上 `S` 才敢跨行；`\1` 回指标签名，防 `</script>` 早闭合）。
_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
#: 这些标签换行留下：段落与列表项之间断开，读的人才看得出这是一封邮件不是一串词。
_BREAK_TAGS = re.compile(r"</?(?:br|p|div|li|tr|td|h[1-6])\b[^>]*>", re.I)
_ANY_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """HTML → 人话：丢脚本与样式整块、去标签、还原实体、空行折掉。"""
    text = _SCRIPT_OR_STYLE.sub(" ", raw)
    text = _BREAK_TAGS.sub("\n", text)
    text = html_unescape(_ANY_TAG.sub("", text))
    return "\n".join(line for line in (one.strip() for one in text.splitlines()) if line).strip()


def _part_text(part: Message) -> str:
    """一段 MIME 的解码文本。它自己声明的字符集优先，认不出来才退回 utf-8。"""
    try:
        payload = part.get_payload(decode=True)
    except (TypeError, ValueError):
        return ""
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"

    try:
        return payload.decode(charset, errors="replace").strip()
    except LookupError:
        return payload.decode("utf-8", errors="replace").strip()


def body_of(message: Message) -> tuple[str, str]:
    """返回 **(text, kind)**：`kind` ∈ `plain`／`html`／`none`。

    `plain` 是发信人自己给的纯文本，**有就用它**——不为"HTML 更完整"反过来，那会拿我们啃出来
    的文本换掉人家写好的那一版。`none` 是一个读数（这封没有可读正文，通常就是纯附件邮件），
    不是错误，也不许拿标题填一个假正文。
    """
    parts = [message] if not message.is_multipart() else list(message.walk())
    html_body = ""

    for part in parts:
        if part.get_content_maintype() != "text":
            continue
        if part.get("Content-Disposition") in ("attachment", "inline"):
            continue

        subtype = (part.get_content_subtype() or "").lower()
        if subtype == "plain":
            plain = _part_text(part)
            if plain:
                return plain, "plain"
        elif subtype == "html" and not html_body:
            html_body = html_to_text(_part_text(part))

    return (html_body, "html") if html_body else ("", "none")

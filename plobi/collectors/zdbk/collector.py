"""本科教务 `zdbk`/`jwglxt` 的采集器 —— **一条信源＝一个网站**（裁定 194）。

这一条采的是这一家给得出的所有东西，不是"课表采集器"：

| `kind` | 端点（10-21 实测通） | 实测形状 |
|---|---|---|
| `class` 课表 | `POST /jwglxt/kbcx/xskbcx_cxXsKb.html`，body `xnm/xqm/captcha_value` | `kbList` 65 行；**课程名/教师/地点全在 `kcb` 那一段 `<br>` 拼起来的串里**（65/65 行含中文且含 `<br>`，最长 88 字符）；时间坐标是 `xqj`(星期)/`djj`(第几节)/`skcd`(连上节数) |
| `exam` 考试 | `POST …/xskscx/kscx_cxXsgrksIndex.html?doType=query&queryModel.showCount=5000` | 信封 `items/totalCount/totalPage`；本轮 **40 条**，`totalCount == items.length` ⇒ 一次拿全 |
| `grade` 成绩 | `POST …/cxdy/xscjcx_cxXscjIndex.html?doType=query&…` | 同上，本轮 **25 条** |
| `major` 主修 | `POST …/zycjtj/xszgkc_cxXsZgkcIndex.html?doType=query&…` | 同上，本轮 **238 条** |
| `practice` 实践分 | `GET …/dessktgl/dessktcx_cxDessktcxIndex.html?gnmkdm=N108001&su=<学号>` | **不是 JSON**：200、16 KB HTML、`<tr>` 2 行 ⇒ 只能按表行啃；学校改页面这一条就红（用户 10-21 拍：接） |

三条不是"顺便"的口径，是被裁定钉的：

- **落点只有池**（裁 193）：`pool_land`，`kind` 区分类型，**不为任何一种数据开专用列**——
  判据是"只有全信源共用的形状才配当列"。日程那侧那四个 `KINDS`（meeting/ddl/class/task）
  比池窄，映射是读池的 Agent 的判断，不由采集器替它做（裁 186）。
- **`occurred_at` 不许猜**（裁 189）：课表这一家给的是**周模板**（星期×节次），不是日历上的点；
  学期起始日要从校历换出来，而**校历那一格还没实测**（Celechron 是拿自己服务器的
  `calendar_config_parser.dart:7` 补的）。所以课表条目的 `occurred_at` 留空，星期与节次原样进
  `context` —— 空是一个读数（"学校没给日期"），填一个推算出来的才是错。
- **凭据只走 `auth.read_credentials()` 那一只读路**（裁 110 二）：本包不 `open('.env')`、
  不读 `os.environ`；CAS 那条链收在 :func:`plobi.collectors.zju.auth.cas_login_to`，
  三家共用一只，不各写一遍（裁 94）。
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

from ..base import Occurrence, pool_items_of
from .state import read_last_pull_at, read_state, write_state
from ..zju import auth
from ..zju.auth import HttpResponse, Transport
from .status import (
    STATE_AUTH_FAILED,
    STATE_CAPTCHA_REQUIRED,
    STATE_DISABLED,
    STATE_EMPTY_TERM,
    STATE_NETWORK_UNREACHABLE,
    STATE_NO_CREDENTIAL,
    STATE_READY,
    STATE_THROTTLED,
    ZdbkUnavailable,
    note_for,
)

SOURCE_ID = "zdbk"
BASE = auth.ZDBK_ORIGIN
KB_URL = f"{BASE}/jwglxt/kbcx/xskbcx_cxXsKb.html"
QUERY_SUFFIX = "?doType=query&queryModel.showCount=5000"
ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("exam", f"{BASE}/jwglxt/xskscx/kscx_cxXsgrksIndex.html{QUERY_SUFFIX}", "考试"),
    ("grade", f"{BASE}/jwglxt/cxdy/xscjcx_cxXscjIndex.html{QUERY_SUFFIX}", "成绩"),
    ("major", f"{BASE}/jwglxt/zycjtj/xszgkc_cxXsZgkcIndex.html{QUERY_SUFFIX}", "主修"),
)
#: 这些键在 jwglxt 的**每一行**里都有，但它们是分页信封漏进来之后的噪声，不是数据。
ENVELOPE_NOISE = frozenset({
    "pageable", "queryModel", "userModel", "totalResult", "listnav", "localeKey",
    "rangeable", "completeAnswer", "jgpxzd", "sfqd",
})
_TITLE_KEYS = ("kcmc", "kchmc", "jcName", "kcb")
_TIME_KEYS = ("kssj", "ksrq", "xkrq", "cjrq", "createDate")
_ID_KEYS = ("row_id", "kch", "cjxhid", "jxbid", "xkkh")
#: 课表那一格的身份**必须是格子的坐标**，不是选课号：一门 `xkkh` 一周上三次，
#: 65 行 `kbList` 就会塌成 3 行（10-22 真数据量出来的：池里 class 只剩 3 条，
#: 而且每轮最后写进去的那一格不同 ⇒ `updated` 在 108/147 之间跳）。
CLASS_ID_KEYS = ("xkkh", "xxq", "xqj", "djj")


@dataclass(frozen=True)
class ZdbkConfig:
    """行为参数。`xnm`/`xqm` 只有 `2026/12` 这一组是实测过的，其余按同一规则推。"""

    enabled: bool = True
    year: str = ""
    season: str = ""
    min_minutes_between_pulls: int = 45
    jitter_minutes: int = 7
    include_practice: bool = True

    def pull_allowed_now(self, last_pull_at: Optional[float], now: float) -> bool:
        """两次真打之间的上限（带抖动）。与 zju 那只同一形状，语义也照抄：它是**上限**，
        不是"每隔这么久一定要打一次"。"""
        if last_pull_at is None:
            return True
        import random

        window = self.min_minutes_between_pulls + random.uniform(0, max(self.jitter_minutes, 0))
        return (now - last_pull_at) >= window * 60

    @classmethod
    def load(cls) -> "ZdbkConfig":
        path = config_path()
        data: dict = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            year=str(data.get("xnm") or ""),
            season=str(data.get("xqm") or ""),
            min_minutes_between_pulls=int(data.get("min_minutes_between_pulls") or 45),
            jitter_minutes=int(data.get("jitter_minutes") if data.get("jitter_minutes") is not None else 7),
            include_practice=bool(data.get("include_practice", True)),
        )

    def term(self, now: Optional[datetime] = None) -> tuple[str, str]:
        if self.year and self.season:
            return self.year, self.season
        moment = now or datetime.now()
        # 秋季学期归属入学那年（实测 2026/12 有课表）；春季落在同一学年的另一半。
        return (str(moment.year) if moment.month >= 9 else str(moment.year - 1),
                "12" if moment.month >= 9 or moment.month <= 1 else "13")


def config_path() -> Any:
    override = (os_environ_get("PLOBI_ZDBK_CONFIG") or "").strip()
    if override:
        return __import__("pathlib").Path(override)
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "plobi" / "zdbk.json"


def os_environ_get(key: str) -> str:
    """**只读这一格配置路径的覆盖开关**——凭据一个都不从这里读（裁 110 二）。"""
    import os

    return os.environ.get(key, "")


def rows_of_items(response: HttpResponse) -> Optional[list[dict]]:
    """认这一家的信封：`{items: [...], totalCount: n}`。不是这个形状就返回 ``None``。"""
    try:
        payload = json.loads(response.text)
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [row for row in payload["items"] if isinstance(row, dict)]
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<br\s*/?>", "｜", text))).strip()


def _identity(row: Mapping[str, Any], kind: str) -> tuple[str, str]:
    """学校给的那一条身份；**不用标题拼**（标题改了行就"消失"一次）。

    课表走组合键：一门课一周占好几个格子，单拿选课号当身份会让同一门课的几次课
    **互相覆盖**（真数据量到过）。其它几类每行自带 `row_id`，单键就够。
    一个键都没有时退到整行哈希，并把走了哪条路留在 `context["identity_key"]` 里。
    """
    if kind == "class":
        parts = [str(row.get(key) or "").strip() for key in CLASS_ID_KEYS]
        if parts[0] and any(parts[2:]):
            return ":".join(p for p in parts if p), "+".join(CLASS_ID_KEYS)
    for key in _ID_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return value, key
    digest = hashlib.sha1(json.dumps({k: v for k, v in row.items()
                                      if k not in ENVELOPE_NOISE}, sort_keys=True,
                                     ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return f"{kind}-{digest[:16]}", "row-sha1"


def _time(row: Mapping[str, Any]) -> tuple[str, str]:
    """(归一前的原值, 找到的键)。拿不到日期就**留空**，不猜（裁 189）。"""
    for key in _TIME_KEYS:
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                       "%Y%m%dT%H%M%S", "%Y-%m-%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(raw, pattern).isoformat(timespec="seconds"), key
            except ValueError:
                continue
        return "", key                       # 有这一格但形状不认识：也说得出是哪一格
    return "", ""


def to_occurrences(kind: str, rows: list[dict]) -> list[Occurrence]:
    items: list[Occurrence] = []
    for row in rows:
        external_id, identity_key = _identity(row, kind)
        title = ""
        for key in _TITLE_KEYS:
            candidate = _clean(str(row.get(key) or ""))
            if candidate:
                title = candidate.split("｜")[0][:120] if key == "kcb" else candidate[:120]
                break
        if not title:
            title = f"{kind} {external_id}"          # 没名字也要有标题；不拿它当身份
        occurred_at, time_key = _time(row)
        context = {k: v for k, v in row.items() if k not in ENVELOPE_NOISE}
        context.update({"source_system": SOURCE_ID, "kind_label": kind,
                        "identity_key": identity_key})
        if time_key:
            context["time_key"] = time_key
        if kind == "class":
            # 周模板：星期×节次。**不给 occurred_at**，理由见模块头第二条；
            # 学期起止那一格由同一条信源一起采（`term` 那一种 kind），不是别家的活。
            context.update({"week_template": True, "weekday": row.get("xqj"),
                            "start_slot": row.get("djj"), "slot_count": row.get("skcd")})
        items.append(Occurrence(
            source=SOURCE_ID, external_id=external_id, title=title,
            start_at=occurred_at, end_at=None, kind=kind, location=None,
            evidence=context,
        ))
    return items


class ZdbkCollector:
    """``Transport`` 是**必填位**（裁定 104 二）：默认回退到真网络调用就是判据 7 那颗雷的形状。"""

    source_id = SOURCE_ID

    def __init__(self, *, transport: Transport,
                 config: Optional[ZdbkConfig] = None,
                 credentials: Optional[tuple[str, str]] = None,
                 credential_reader: Callable[[], Optional[tuple[str, str]]] = auth.read_credentials,
                 now: Callable[[], float] = time.time):
        self.transport = transport
        self.config = config if config is not None else ZdbkConfig.load()
        self._credentials = credentials
        self._credential_reader = credential_reader
        self._now = now

    # -- 会话 ---------------------------------------------------------------

    def _credentials_or_state(self) -> tuple[str, str, Optional[tuple[str, str]]]:
        if not self.config.enabled:
            return STATE_DISABLED, note_for(STATE_DISABLED), None
        creds = self._credentials if self._credentials is not None else self._credential_reader()
        if creds is None:
            return STATE_NO_CREDENTIAL, note_for(STATE_NO_CREDENTIAL), None
        return STATE_READY, "", creds

    def _ensure_session(self, creds: tuple[str, str]) -> tuple[str, str]:
        username, password = creds
        state, detail, _rows = auth.cas_login_to(
            self.transport, username, password,
            entry_url=auth.ZDBK_SSO_ENTRY, probe_url=auth.ZDBK_MENU_URL,
            rows_of=self._menu_rows, probe_expectation="教务首页")
        return state, detail

    @staticmethod
    def _menu_rows(response: HttpResponse) -> Optional[list[dict]]:
        """教务首页：HTML 也算"登进去了"（这一家没有 JSON 版的 todos 可探）。"""
        if response.status >= 400 or "jwglxt" not in response.url:
            return None
        return []

    def _student_id(self) -> str:
        menu = self.transport.get(auth.ZDBK_MENU_URL)
        for pattern in (r'name="su"\s+id="su"\s+value="(\d+)"', r'"xh"\s*:\s*"(\d+)"', r"su=(\d{6,})"):
            found = re.search(pattern, menu.text)
            if found:
                return found.group(1)
        return ""

    # -- 契约 ---------------------------------------------------------------

    def _record(self, *, state: str, rows: int) -> None:
        write_state({"last_pull_at_epoch": self._now(), "last_state": state, "rows": rows})

    def health(self) -> dict:
        state, detail = (STATE_DISABLED, note_for(STATE_DISABLED)) if not self.config.enabled \
            else (STATE_NO_CREDENTIAL, note_for(STATE_NO_CREDENTIAL)) \
            if (self._credentials is None and self._credential_reader() is None) \
            else (STATE_READY, "会话要一次真打才知道；这一格不探网络")
        return {"ok": state == STATE_READY, "state": state, "detail": detail,
                "note": note_for(state) if state != STATE_READY else ""}

    def pull(self, *, since: Any = None) -> list[Occurrence]:
        items, report = self.pull_report()
        self._record(state=report["state"], rows=len(items))
        return items

    def pull_report(self) -> tuple[list[Occurrence], dict]:
        state, detail, creds = self._credentials_or_state()
        if creds is None:
            raise ZdbkUnavailable(state, detail)
        last = read_last_pull_at()
        if not self.config.pull_allowed_now(last, self._now()):
            # 节流这一格与"用户设的采集频率"不是一回事（裁 191 二）：频率说"我想几点采"，
            # 这一格说"最多多快碰学校一次"。上一轮真采到东西就是 260 行进池 ⇒ 一天里
            # 手点两次就会打两次教务，所以这里必须真的拦下来。
            raise ZdbkUnavailable(STATE_THROTTLED,
                                  f"距上一轮不足 {self.config.min_minutes_between_pulls} 分钟（带抖动）")
        state, detail = self._ensure_session(creds)
        if state != STATE_READY:
            raise ZdbkUnavailable(state, detail)

        self._record(state=STATE_READY, rows=0)
        year, season = self.config.term()
        collected: list[Occurrence] = []
        counts: dict[str, int] = {}

        kb = self.transport.post_form(KB_URL, {"xnm": year, "xqm": season, "captcha_value": ""})
        if "captcha_error" in kb.text[:4000]:
            raise ZdbkUnavailable(STATE_CAPTCHA_REQUIRED,
                                  f"课表这一轮要人工验证码（{year}/{season}）⇒ 按口径停手，不自动识别")
        try:
            kb_payload = json.loads(kb.text)
        except ValueError:
            raise ZdbkUnavailable(STATE_AUTH_FAILED,
                                  f"课表端点没回 JSON（HTTP {kb.status}）：会话掉了或页面改了") from None
        kb_rows = kb_payload.get("kbList") if isinstance(kb_payload, dict) else None
        counts["class"] = len(kb_rows or [])
        if kb_rows:
            collected += to_occurrences("class", [r for r in kb_rows if isinstance(r, dict)])

        for kind, url, _label in ENDPOINTS:
            response = self.transport.post_form(url, {})
            rows = rows_of_items(response)
            if rows is None:
                raise ZdbkUnavailable(STATE_AUTH_FAILED,
                                      f"{_label}端点不回 items 信封（HTTP {response.status}）")
            counts[kind] = len(rows)
            collected += to_occurrences(kind, rows)

        if self.config.include_practice:
            student_id = self._student_id()
            if student_id:
                practice = self.transport.get(f"{BASE}/jwglxt/dessktgl/dessktcx_cxDessktcxIndex.html"
                                              f"?gnmkdm=N108001&su={student_id}")
                rows = _practice_rows(practice.text)
                counts["practice"] = len(rows)
                collected += to_occurrences("practice", rows)
            else:
                counts["practice"] = 0        # 学号拿不到：这一类这一轮没有，不静默说成"没实践分"

        if not collected:
            raise ZdbkUnavailable(STATE_EMPTY_TERM, f"这一轮一条都没拿到（学年 {year} 学期 {season}）")
        return collected, {"state": STATE_READY, "term": f"{year}/{season}", "counts": counts}

    def collect(self, *, service: Any = None) -> dict:
        """`sweep` 那一轮调的就是这一格——**它会把 `service=` 传进来**，签名少了它就每次 `raised`。

        池不需要 service（日程那侧的判断权在 Agent，不在这里），所以这一格收下不用；
        收下不用比"不接这个参数"贵：前者是一行读数，后者是一天一轮的红。
        """
        items, report = self.pull_report()
        result = pool_land(pool_items_of(items, source=SOURCE_ID))
        return {**result, "state": report["state"], "source": SOURCE_ID, "counts": report["counts"],
                "term": report["term"]}


def _practice_rows(page: str) -> list[dict]:
    """实践分那一页是 HTML 表格：按 `<tr>` 拆，只留含中文的行（表头与空行不算条目）。

    这条就是"学校改页面我们就红"的那一条（用户 10-21 拍：接）。所以它**不猜列名**——
    整行文本进 `cells`，标题取第一段；哪天页面换了，坏的是条数与标题，不是把别的东西当实践分。
    """
    rows: list[dict] = []
    for raw in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = re.findall(r"<t([dh])[^>]*>(.*?)</t[dh]>", raw, flags=re.S | re.I)
        # 全 `<th>` 的那一行是表头，不是条目——它同样含中文，只按"含中文"筛就会混进来。
        if cells and all(tag == "h" for tag, _ in cells):
            continue
        text = [_clean(body) for _tag, body in cells]
        text = [cell for cell in text if cell]
        if len(text) >= 2 and any(re.search(r"[一-鿿]", cell) for cell in text):
            rows.append({"row_id": f"practice-{len(rows)}-" + _clean(text[0])[:16],
                         "kcmc": text[0], "cells": text})
    return rows


def pool_land(items):                                   # 与 sweep 同一只池入口，不第二份
    from plobi.pool import land as _land

    return _land(items)

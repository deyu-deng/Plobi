"""本科教务 `zdbk`/`jwglxt` 的采集器 —— **一条信源＝一个网站**（裁定 194）。

这一条采的是这一家给得出的所有东西，不是"课表采集器"：

| `kind` | 端点（10-21 实测通）／来源 | 实测形状 |
|---|---|---|
| `class` 课表 | `POST /jwglxt/kbcx/xskbcx_cxXsKb.html`，body `xnm/xqm/captcha_value` | `kbList` 65 行；**课程名/教师/地点全在 `kcb` 那一段 `<br>` 拼起来的串里**（65/65 行含中文且含 `<br>`，最长 88 字符）；时间坐标是 `xqj`(星期)/`djj`(第几节)/`skcd`(连上节数) |
| `exam` 考试 | `POST …/xskscx/kscx_cxXsgrksIndex.html?doType=query&queryModel.showCount=5000` | 信封 `items/totalCount/totalPage`；本轮 **40 条**，`totalCount == items.length` ⇒ 一次拿全 |
| `grade` 成绩 | `POST …/cxdy/xscjcx_cxXscjIndex.html?doType=query&…` | 同上，本轮 **25 条** |
| `major` 主修／学业跟踪 | `POST …/zycjtj/xszgkc_cxXsZgkcIndex.html?doType=query&…` | 同上，本轮 **238 条**；其中 **217 条没有 `xkkh`**（计划内课程，不是选课记录） |
| `practice` 实践分 | `GET …/dessktgl/dessktcx_cxDessktcxIndex.html?gnmkdm=N108001&su=<学号>` | **不是 JSON**：200、16 KB HTML、`<tr>` 2 行 ⇒ 只能按表行啃；学校改页面这一条就红（用户 10-21 拍：接） |
| `term` 学期 | **归并自 `major` 的 `xn`/`xq`/`xqmc`** | 这一格说的是"哪一学年哪一学期有课"——学校在主修每一行里说了自己在哪个学期，但没有 id，所以这里是把那些行**归并**成学期条目（记录数与学分在里面）。⚠️ 10-22 15:3x 那一版把这格写成"学校没有校历端点可采"，那句当天下午被推翻了：校历在，见下面 `school_calendar` 那一行；`term` 这一族仍然没有日子，因为**主修那些行里确实没有日子**，那仍是一个读数 |
| `school_calendar` 校历 | `GET /jwglxt/xtgl/index_cxXiaoLi.html` | 10-22 23:5x 实测：200、17 KB HTML，表头那两格写着「2026-2027学年秋学期(2026-09-14至2026-11-08)」与「冬学期(2026-11-09至2027-02-21)」⇒ **学期起止日子是学校自己写在那一页上的**，一学期进一行，`start_at`/`end_at` 就是那两个串。那一页下面还有一张 周次×星期×日 的格子表（15 周 × 7 天），**这一版没啃**：它靠合并单元格对齐，正则啃它等于赌学校不改表格；有了学期起始日，"第 N 周周几是几号"在读侧是一步换算 |
| `practice_course` 实践环节 | 课表回包里的 `sjkList` | 实测 2 行；落进池里读作 `blueS军训[李睿]秋blueF`、`blueS汽车技术综合实践[郑旭/熊树生]短blueF`（`blueS`/`blueF` 是站方给着色用的裸标记）⇒ 这一格是**实践环节课程**。⚠️ 我上一版按 `sjk` 这个键名把它猜成了"上下课钟点"，10-22 16:3x 真采一轮纠正过来。**"第几节是几点"这一格在这一家仍然没找到**——那是个空缺的读数，不是采到的东西 |

三条不是"顺便"的口径，是被裁定钉的：

- **落点只有池**（裁 193）：`pool_land`，`kind` 区分类型，**不为任何一种数据开专用列**——
  判据是"只有全信源共用的形状才配当列"。日程那侧那四个 `KINDS`（meeting/ddl/class/task）
  比池窄，映射是读池的 Agent 的判断，不由采集器替它做（裁 186）。
- **`occurred_at` 不许猜**（裁 189）：课表这一家给的是**周模板**（星期×节次），不是日历上的点，
  所以课表行的 `occurred_at` 留空，星期、节次、学年学期码原样进 `context` ——
  **空是一个读数**（"学校在这一行里没给日子"），填一个推算出来的才是错。
  日子本身有来源了：同一家的校历那一页（上表 `school_calendar`）写着学期起止，那两个串照原样进池。
  **"第 N 周周几是几号"仍然不许我在写入口算**——那是把两族数据拼成一个新事实，判权在读池那侧（裁 186）。
- **凭据只走 `auth.read_credentials()` 那一只读路**（裁 110 二）：本包不 `open('.env')`、
  不读 `os.environ`；CAS 那条链收在 :func:`plobi.collectors.zju.auth.cas_login_to`，
  三家共用一只，不各写一遍（裁 94）。
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import random
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
#: 校历那一页（10-22 23:5x 实测：200、17 KB HTML、表头那两格写着
#: 「2026-2027学年秋学期(2026-09-14至2026-11-08)」与「冬学期(2026-11-09至2027-02-21)」）。
#: 这一格纠正了本包 10-22 15:3x 写下的「学校没有校历端点可采」——那句当时是对**首页**那一条路说的，
#: 端点本身在 `xtgl/index_v510335.js` 里，不在首页 HTML 上（抠法见 `D:/Temp/probe-jiaowu-round10.py`）。
CALENDAR_URL = f"{BASE}/jwglxt/xtgl/index_cxXiaoLi.html"
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
_TITLE_KEYS = ("kcmc", "sjkcmc", "kchmc", "jcName", "kcb")
_TIME_KEYS = ("kssj", "ksrq", "xkrq", "cjrq", "createDate")
#: 结束那一格：校历那两列写的是「2026-09-14至2026-11-08」，`jssj`（结束日期）是学校给的键名，
#: 与 `kssj`（开始）成对。课表/考试那几族没有这一格 ⇒ 取不到就还是 `None`，不是空串。
_END_KEYS = ("jssj", "jzrq")
_TIME_PATTERNS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                  "%Y%m%dT%H%M%S", "%Y-%m-%d", "%Y年%m月%d日")
#: 单键退路。**`row_id` 不在里面**：它是正方给每行编的**页内序号**（10-22 实测：主修 238 行里
#: 217 行连 `xkkh` 都没有，唯一能唯一化它们的只有这个序号，值是纯数字）。拿它当身份就是拿
#: **位置**当身份——排序一变整族换身份，而两个端点的序号撞在一起时池里会静默少一行。
_ID_KEYS = ("kch", "cjxhid", "jxbid", "xkkh")
#: 课表那一格的身份**必须是格子的坐标**，不是选课号：一门 `xkkh` 一周上三次，
#: 65 行 `kbList` 就会塌成 3 行（10-22 真数据量出来的：池里 class 只剩 3 条，
#: 而且每轮最后写进去的那一格不同 ⇒ `updated` 在 108/147 之间跳）。
CLASS_ID_KEYS = ("xkkh", "xxq", "xqj", "djj")
#: **每个端点各自**的身份键组：按顺序试，第一组"每个键都非空"就用它。
#:
#: 为什么不许一条通用链服务全部端点：`pool_items` 的唯一键是 `(source, external_id)`，
#: **`kind` 不在键里**，而这一家每个端点给的"那一条 id"是形状完全不同的东西。通用链的
#: 现读后果就是那 46 行被盖掉的账（学校给 考试 40 / 成绩 25，池里只剩 15 / 4）。
#:
#: 每条后面的分数是**第五轮探查（10-22 15:3x，真账号一轮，只读）量的"去重几条／学校给几条"**，
#: 不是推的。走到哪条路都写在 `context.identity_key` 里；哪条都不成立 ⇒ 退整行内容哈希，
#: 并由 :func:`collapse_of` 在这一轮的读数里说清"这一族有没有行被盖掉"——不静默。
IDENTITY_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    # 65/65。注意 `xkkh+xqj+djj` 只有 **64/65**：同一格换过一次上法（`skcd` 连上节数不同）
    # 就会被合成一条 —— 这是 10-22 那笔"改对但没改全"的账，现在补上。
    "class": (("xkkh", "xxq", "xqj", "djj", "skcd"), CLASS_ID_KEYS),
    # 40/40；光 `xkkh` 今天也 40/40，但 08:15 那一轮它只给过 15 条 ⇒ 带上考试时间更严
    "exam": (("xkkh", "kssj"), ("xkkh",)),
    # 25/25。这一格学校**既没给学期也没给别的 id**：同一门课多条成绩（重修、按次给分）
    # 只能靠分数与绩点这一组区分开——这是"没有稳定身份"的读数，不是好设计，照实记。
    "grade": (("xkkh", "cj", "jd"), ("xkkh",)),
    # 学业跟踪：238 行里 **217 行没有 `xkkh`**（那是计划内的课程，不是选课记录）
    # ⇒ 按"课程代码＋学年＋学期"认；不成立就退内容哈希，**不用页内序号**。
    "major": (("kcdm", "xn", "xq"), ("xkkh",)),
    # 学期是从主修那 238 行里归并出来的（一学年一学期一条），键就是学校给的那一对码
    "term": (("xn", "xq"),),
    # 校历那一页（10-22 实测）：学校没给 id，但它在那一格写明了「哪一学年的哪一学期」——
    # 那一对就是这一族的身份，与 `term` 那几行是两套表述、各说各话，**不硬并**（理由见 :func:`calendar_rows`）
    "school_calendar": (("xn", "xq"),),
    # 实践环节课程（`sjkList`，10-22 真采量的：`sjkcmc` 是 `blueS军训[李睿]秋blueF` 那种串）：
    # 学校没给 id，名称就是这一格的键
    "practice_course": (("sjkcmc",),),
}


@dataclass(frozen=True)
class ZdbkConfig:
    """行为参数。`xnm`/`xqm` 只有 `2026/12` 这一组是实测过的，其余按同一规则推。"""

    enabled: bool = True
    year: str = ""
    season: str = ""
    min_minutes_between_pulls: int = 45
    jitter_minutes: int = 7
    include_practice: bool = True
    #: 两次外呼之间歇多久（秒，随机取区间）。这一家没有开放接口，一次 `pull()` 要打六七个请求，
    #: 全挤在同一秒里就是"把站方当数据库刷"。参照件里唯一值得照抄的就是这一格——Python 那个
    #: CLI（`celekli.py:641 --time-coeff`，默认 `1.5/5`）把礼貌延时做成了参数；
    #: Celechron 本体没做（它的节流在外层：单飞 + 15 分钟一次的系统作业）。
    request_gap_seconds: tuple[float, float] = (1.5, 4.5)

    def pull_allowed_now(self, last_pull_at: Optional[float], now: float) -> bool:
        """两次真打之间的上限（带抖动）。与 zju 那只同一形状，语义也照抄：它是**上限**，
        不是"每隔这么久一定要打一次"。"""
        if last_pull_at is None:
            return True
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
            request_gap_seconds=(float(data.get("request_gap_lo") if data.get("request_gap_lo") is not None else 1.5),
                                 float(data.get("request_gap_hi") if data.get("request_gap_hi") is not None else 4.5)),
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
    """把学校给的那段文本清成能读的一行：`<br>` 换成 `｜`，实体解开，再把空白压平。

    `blueS`/`blueF` 那两枚**裸标记要一起剥掉**（10-22 真数据读到的：站方给着色用的前后缀，
    如 `blueS军训[李睿]秋blueF`）。它不在任何 HTML 标签里，`<…>` 那套正则抓不到，
    不专门剥就会原样长进标题里。
    """
    stripped = re.sub(r"(?<![A-Za-z0-9_])(blueS|blueF|redS|redF|greenS|greenF)(?![A-Za-z0-9_])",
                      "", text)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<br\s*/?>", "｜", stripped))).strip()


def _identity(row: Mapping[str, Any], kind: str) -> tuple[str, str]:
    """学校给的那一条身份；**不用标题拼**（标题改了行就"消失"一次）。

    三条路，按"这家的这一格到底给不给稳定 id"排：

    1. :data:`IDENTITY_KEYS` 里**这个端点**那一族键组（每个端点各量各的，理由与分数写在那张表上）；
    2. 单键链（`kch`／`cjxhid`／`jxbid`／`xkkh`）。**`row_id` 不在这条链上**：它是正方给每行编的
       页内序号（10-22 实测主修 238 行里 217 行只有它是唯一的，值是纯数字），拿位置当身份 ⇒
       排序一变整族换身份、两个端点的序号还会互相盖；
    3. 一个键都没有 ⇒ 整行内容哈希，并把这条路写在 `context.identity_key` 里（读数，不是猜）。
       实践分与"主修里那 217 行没有 `xkkh` 的"走的就是这一条——**这是学校没给身份**的形状，
       不是我嫌麻烦。

    `external_id` 一律带 `kind` 那一层命名空间：池的唯一键是 `(source, external_id)`，
    两个端点各自合法的 id 撞在一起时，**少掉的那一行不会报错，只是不见了**。
    """
    for keys in IDENTITY_KEYS.get(kind, ()):
        parts = [str(row.get(key) or "").strip() for key in keys]
        if all(parts):
            return f"{kind}:" + ":".join(parts), "+".join(keys)
    if kind == "class":
        # 课表退路：格子坐标里 `xkkh` 必须在，但学期/星期/节次可能缺任一格
        parts = [str(row.get(key) or "").strip() for key in CLASS_ID_KEYS]
        if parts[0] and any(parts[2:]):
            return "class:" + ":".join(p for p in parts if p), "+".join(CLASS_ID_KEYS)
    for key in _ID_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return f"{kind}:{value}", key
    digest = hashlib.sha1(json.dumps({k: v for k, v in row.items()
                                      if k not in ENVELOPE_NOISE}, sort_keys=True,
                                     ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return f"{kind}-sha1-{digest[:16]}", "row-sha1"


def _as_iso(raw: str) -> str:
    """认得的日子归一成 ISO；认不得就回空串（**不猜**，裁 189）。"""
    for pattern in _TIME_PATTERNS:
        try:
            return datetime.strptime(raw, pattern).isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def _first_time(row: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[str, str]:
    """(归一后的值, 找到的键)。拿不到日子就**留空**，并且说得出是在哪一格上没拿到（裁 189）。"""
    for key in keys:
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        return _as_iso(raw), key                    # 有这一格但形状不认识：也说得出是哪一格
    return "", ""


def _time(row: Mapping[str, Any]) -> tuple[str, str]:
    return _first_time(row, _TIME_KEYS)


def _end_time(row: Mapping[str, Any]) -> tuple[str, str]:
    return _first_time(row, _END_KEYS)


def collapse_of(kind: str, rows: list[dict]) -> tuple[int, int]:
    """(学校给的行数, 算出来的身份条数)。**不相等就是这一族有行被盖掉**。

    这一对数是本刀给自己加的牙齿，起因是 10-22 那两次真数据账：课表 65→3、考试 40→15、成绩 25→4，
    三种都是**同一件事**（身份撞了），而当时池里、日志里、设置面上**一个字都没有**——少了 46 行
    这件事只有把池的数字与学校的数字并排放在一起才看得见。现在每一轮都并排放着（`report["collapsed"]`），
    不安静。
    """
    return len(rows), len({_identity(row, kind)[0] for row in rows})


def terms_from(major_rows: list[dict]) -> list[dict]:
    """学期这一格：把主修那 238 行里**学校自己说出来的学期**归并成条目（一学年一学期一条）。

    用户 10-22 的话是「zdbk 的采集器应该包含学期的采集」。⚠️ 这一格的来历里有一次改判：
    10-22 15:3x 那一版我写的是"这一家没有校历端点可采"（当时只翻了教务首页那 18 KB 的壳与两家 Dart，
    菜单 0 条、动作 URL 1 个），当晚被自己的第六轮探查推翻——校历在 `xtgl/index_v510335.js` 里挂着，
    就是同上的 `school_calendar` 那一行。**这一族照旧收**：它说的是"哪一学年哪一学期有课、几门、几分"，
    那是主修行里唯一说得出学期的东西，而校历那一页说的是日子。两族各说各的（为什么不并：见 :func:`calendar_rows`）。

    **起止日期在这一族仍然留空**（裁 189：空是一个读数）——那两个串不在主修这些行里。
    """
    by_term: dict[tuple[str, str], dict] = {}
    for row in major_rows:
        year = str(row.get("xn") or "").strip()
        season = str(row.get("xq") or "").strip()
        if not (year and season):
            continue
        name = str(row.get("xqmc") or "").strip()
        slot = by_term.setdefault((year, season), {
            "kcmc": f"{year} {name}".strip(), "xn": year, "xq": season, "xqmc": name,
            "records": 0, "credits": 0.0})
        slot["records"] += 1
        try:
            slot["credits"] = round(slot["credits"] + float(row.get("xf") or 0), 2)
        except (TypeError, ValueError):
            pass
    return sorted(by_term.values(), key=lambda item: (item["xn"], item["xq"]), reverse=True)


#: 校历那一页表头里的学期格：「2026-2027学年秋学期(2026-09-14至2026-11-08)」。
#: 全角括号与半角都见过（站方模板里这两格同一形状），所以两类都吃。
_CALENDAR_TERM = re.compile(
    r"(?P<xnm>\d{4}-\d{4})学年(?P<xqmc>[\u4e00-\u9fff]{1,3})学期\s*[（(]"
    r"(?P<start>\d{4}-\d{2}-\d{2})至(?P<end>\d{4}-\d{2}-\d{2})[）)]")


def calendar_rows(page: str) -> list[dict]:
    """校历那一页 → 一学期一行，**起止日子是学校自己写在那一页表头上的那两个串**。

    为什么这一族值得单独立一种 `kind` 而不是塞进 `term`：`term` 那几行是从主修 238 行里归并出来的
    「哪一学年哪一学期有课」，用的是 `xn`/`xq` 那一对**码**（`2025`/`1`）；校历这一页说的是
    「2026-2027 学年秋学期从 9 月 14 日到 11 月 8 日」，用的是**学年区间加学期名**。两套表述不是同一个 id，
    硬并到一起就是拿我的拼接当身份（裁 194 那条判据反对的正是这个）。两边都进池，
    "它们是同一个学期"由读池那侧去认——它有两种表示可比，比只有一种好。

    认不出来（0 行）是**一个读数**，不抛：这一族没采到，别的族照进，`counts` 里那一格写 0。
    """
    found: dict[tuple[str, str], dict] = {}
    for match in _CALENDAR_TERM.finditer(page):
        year = match.group("xnm")
        season = match.group("xqmc")
        found.setdefault((year, season), {
            "kcmc": match.group(0), "xn": year, "xq": season,
            "kssj": match.group("start"), "jssj": match.group("end"),
        })
    return list(found.values())


def practice_courses_from(payload: Mapping[str, Any]) -> list[dict]:
    """实践环节课程那一格（课表回包里的 `sjkList`）。

    ⚠️ **这一格是被真数据纠正过一次的地方**：`sjk` 那个键名我先按"时间课表"猜成了上下课钟点，
    10-22 16:3x 真采一轮落进池里，标题读作 `军训[李睿]秋`、`汽车技术综合实践[郑旭/熊树生]短`
    ——那是**实践环节课程**。⇒ 名字叫 `practice_course`。而"第几节是几点"这一格在这一家
    **仍然没找到**：它现在是个空缺的读数，不是采到的东西（P6：不许描述不存在的机制）。
    """
    rows = payload.get("sjkList")
    return [row for row in (rows or []) if isinstance(row, dict)]


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
        ended_at, _end_key = _end_time(row)
        context = {k: v for k, v in row.items() if k not in ENVELOPE_NOISE}
        context.update({"source_system": SOURCE_ID, "kind_label": kind,
                        "identity_key": identity_key})
        if time_key:
            context["time_key"] = time_key
        if kind == "class":
            # 周模板：星期×节次。**不给 occurred_at**，理由见模块头第二条；
            # "哪一学年哪一学期"由同一条信源一起进池（`term` 与 `school_calendar` 那两种 kind，
            # 后者带着学校自己写的起止日子，10-22 实测）——**把学期×星期×节次拼成一个日期是读池
            # 那侧的算术**，不是我在写入口替学校编数据。钟点那一格还没找到。
            context.update({"week_template": True, "weekday": row.get("xqj"),
                            "start_slot": row.get("djj"), "slot_count": row.get("skcd")})
        items.append(Occurrence(
            source=SOURCE_ID, external_id=external_id, title=title,
            start_at=occurred_at, end_at=ended_at or None, kind=kind, location=None,
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
                 now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 spread: Callable[[float, float], float] = random.uniform):
        self.transport = transport
        self.config = config if config is not None else ZdbkConfig.load()
        self._credentials = credentials
        self._credential_reader = credential_reader
        self._now = now
        self._sleep = sleep
        self._spread = spread

    def _polite(self) -> None:
        """请求与请求之间歇一下（区间随机，不是定值——定值就是个更容易被认出来的指纹）。

        测试把 `sleep`/`spread` 注掉，所以这一格**不花真实时间**也能被钉住（判据是"歇了几次、
        每次多久在区间内"，不是"真等了那么久"）。
        """
        low, high = self.config.request_gap_seconds
        if high <= 0:
            return
        self._sleep(max(0.0, float(self._spread(low, high))))

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
        rows_by_kind: dict[str, list[dict]] = {}

        self._polite()
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
        rows_by_kind["class"] = [r for r in (kb_rows or []) if isinstance(r, dict)]
        if rows_by_kind["class"]:
            collected += to_occurrences("class", rows_by_kind["class"])
        # 实践环节课程（`sjkList`）：同一个回包里学校另外说出来的那几行，函数文档串里有那次纠正
        rows_by_kind["practice_course"] = practice_courses_from(kb_payload)
        if rows_by_kind["practice_course"]:
            collected += to_occurrences("practice_course", rows_by_kind["practice_course"])

        for kind, url, _label in ENDPOINTS:
            self._polite()
            response = self.transport.post_form(url, {})
            rows = rows_of_items(response)
            if rows is None:
                raise ZdbkUnavailable(STATE_AUTH_FAILED,
                                      f"{_label}端点不回 items 信封（HTTP {response.status}）")
            rows_by_kind[kind] = rows
            collected += to_occurrences(kind, rows)

        # 学期：学校没有校历端点，但主修每一行都说了自己在哪一学年哪一学期（`xn`/`xq`/`xqmc`）
        # ⇒ 归并成条目。理由与"日期为什么仍留空"写在 :func:`terms_from` 的文档串里。
        rows_by_kind["term"] = terms_from(rows_by_kind.get("major") or [])
        if rows_by_kind["term"]:
            collected += to_occurrences("term", rows_by_kind["term"])

        # 校历：学期起止日子在这一家自己的那一页上（10-22 实测 200 / 17 KB / 表头两格写着日子）。
        # 认不出格子就落 0 行——**这是一个读数**（"这一页没认出学期起止"），不为它把整轮判红，
        # 也不许把日子推算出来冒充学校给的（裁 189）。
        self._polite()
        calendar = self.transport.get(CALENDAR_URL)
        rows_by_kind["school_calendar"] = calendar_rows(calendar.text) if calendar.status < 400 else []
        if rows_by_kind["school_calendar"]:
            collected += to_occurrences("school_calendar", rows_by_kind["school_calendar"])

        if self.config.include_practice:
            self._polite()
            student_id = self._student_id()
            if student_id:
                self._polite()
                practice = self.transport.get(f"{BASE}/jwglxt/dessktgl/dessktcx_cxDessktcxIndex.html"
                                              f"?gnmkdm=N108001&su={student_id}")
                rows_by_kind["practice"] = _table_rows(practice.text)
                collected += to_occurrences("practice", rows_by_kind["practice"])
            else:
                rows_by_kind["practice"] = []   # 学号拿不到：这一类这一轮没有，不静默说成"没实践分"

        # 每一族都并排放两个数：**学校给几行**与**我算出几个身份**。不相等就是这一族有行被盖掉。
        # 这一格是本刀给自己加的牙齿（10-22 那 46 行就是这么静默少的，见 :func:`collapse_of`）。
        counts = {}
        collapsed = {}
        for kind, rows in rows_by_kind.items():
            given, distinct = collapse_of(kind, rows)
            counts[kind] = given
            if given != distinct:
                collapsed[kind] = f"{given} 行 → {distinct} 条身份"

        if not collected:
            raise ZdbkUnavailable(STATE_EMPTY_TERM, f"这一轮一条都没拿到（学年 {year} 学期 {season}）")
        return collected, {"state": STATE_READY, "term": f"{year}/{season}", "counts": counts,
                          "collapsed": collapsed}

    def collect(self, *, service: Any = None) -> dict:
        """`sweep` 那一轮调的就是这一格——**它会把 `service=` 传进来**，签名少了它就每次 `raised`。

        池不需要 service（日程那侧的判断权在 Agent，不在这里），所以这一格收下不用；
        收下不用比"不接这个参数"贵：前者是一行读数，后者是一天一轮的红。
        """
        items, report = self.pull_report()
        result = pool_land(pool_items_of(items, source=SOURCE_ID))
        return {**result, "state": report["state"], "source": SOURCE_ID, "counts": report["counts"],
                "collapsed": report["collapsed"], "term": report["term"]}


def _table_rows(page: str) -> list[dict]:
    """把 jwglxt 那种 HTML 表格啃成行——**列名认表头，不猜列序**。

    为什么不能用"第 0 列是名称、第 3 列是分值"这种写死的列序：学校换一列顺序，写死列序就是把
    A 列的值挂到 B 列的名字下——那是**看起来正常**的错，比报错更难查。认表头那一行，坏了就
    是条数与列名对不上，一眼看得出（"学校改页面我们就红"这一条是用户 10-21 拍的：接）。

    两条不许含糊的形状：
    - **表头行不算条目**（它同样含中文，只按"含中文"筛就会混进来）；
    - **这一格学校从来没给过 id** ⇒ 这里不造 id。以前我按行号拼过一枚 `practice-<序号>-...`，
      那是**位置当身份**：表换个序整族就换身份，池里长出第二份。留空 ⇒ :func:`_identity`
      自己退到"整行内容哈希"，并把走了哪条路写进 `context.identity_key`（那是读数，不是猜）。
    """
    header: list[str] = []
    rows: list[dict] = []
    for raw in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = re.findall(r"<t([dh])[^>]*>(.*?)</t[dh]>", raw, flags=re.S | re.I)
        if not cells:
            continue
        if all(tag == "h" for tag, _ in cells):        # 全 `<th>` ⇒ 表头
            header = [_clean(body) for _tag, body in cells]
            continue
        text = [_clean(body) for _tag, body in cells]
        if len([cell for cell in text if cell]) < 2 or not any(re.search(r"[一-鿿]", c) for c in text):
            continue
        row: dict = {"cells": [cell for cell in text if cell], "kcmc": next((c for c in text if c), "")}
        for name, value in zip(header, text):
            if name and value:
                row[f"列·{name}"] = value
        rows.append(row)
    return rows


def pool_land(items):                                   # 与 sweep 同一只池入口，不第二份
    from plobi.pool import land as _land

    return _land(items)

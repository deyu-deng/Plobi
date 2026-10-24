r"""站点**自报的名字**（`WP-SOURCE-LASTMILE` 规则 1–2）：与图标共用那一次抓取，规矩也共用。

跑法：`bash scripts/run_tests.sh tests/plobi/test_source_site_name.py -q`

三个真读数决定了这一档的形状（都取于 2026-10-24）：

- `https://zdbk.zju.edu.cn/` 是一条 **68 字节的 `<meta http-equiv=refresh>`**，跟一跳才是登录页，
  那一页的 `<title>` 是「本科教学管理信息服务平台」——不跟那一跳就永远读不到名字。
- `https://courses.zju.edu.cn` 对裸 GET 现回 **400**（两种 UA 都试了）——所以"抓不到"是一个
  合法读数，得有下一层退路（声明名），不许把那一行画成没名字。
- `https://www.dingtalk.com` 的 `<title>` 是「钉钉，AI时代工作方式」——**营销话不是信源的名字**，
  所以站名兜底**只对「网站」那一类开**（那条判据在 `sources_api._enrich`，测在
  `test_sources_api_surface.py::TestTheSourceNamesItself`）。

零网络：`opener` 是注入位（裁定 104 二那一格）。
"""

from __future__ import annotations

import pytest

from plobi.collectors import icons


class Reply:
    def __init__(self, body, url: str) -> None:
        # 页面可以是 str 也可以是 bytes：中文页用 str 写才读得动（bytes 字面量装不下中文）。
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return self._body[:n]


class FakeNet:
    """按 URL 回页面的假外网；顺带记每一次请求（抓一次就是抓一次）。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    def __call__(self, request, timeout=None):
        url = request if isinstance(request, str) else request.full_url
        self.requests.append(url)
        # 最长前缀优先：`https://jw.test/` 也是 `https://jw.test/jwglxt` 的前缀，
        # 按插入顺序匹配会把第二跳又送回那一页 refresh。
        for known in sorted(self.pages, key=len, reverse=True):
            if url.startswith(known):
                return Reply(self.pages[known], url)
        raise OSError(f"404 {url}")


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    cache = tmp_path / "icons"
    cache.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_SOURCE_ICON_DIR", str(cache))
    return cache


class TestTheExtraction:
    def test_the_site_s_own_declaration_beats_a_page_title(self) -> None:
        """`og:site_name` 是站方给机器读的那一格；`<title>` 常常是「首页 - 站名」那种句子。"""
        page = '<html><head><meta property="og:site_name" content="本科教务系统">' \
               "<title>首页 | 登录</title></head></html>"

        assert icons._name_from(page) == "本科教务系统"

    def test_a_title_is_cut_into_a_proper_name(self) -> None:
        """「钉钉，AI时代工作方式」→「钉钉」：分隔符后面那句是营销话，不是名字的一部分。"""
        for page, expected in (
            ("<title>钉钉，AI时代工作方式</title>", "钉钉"),
            ("<title>WeChat | Connects with over 1 billion users</title>", "WeChat"),
            ("<title>学在浙大 - 统一登录</title>", "学在浙大"),
        ):
            assert icons._name_from(page) == expected

    def test_an_html_entity_lands_as_the_character_it_means(self) -> None:
        assert icons._name_from("<title>A&amp;B 门户</title>") == "A&B 门户"

    def test_a_long_title_is_capped_not_wrapped(self) -> None:
        """限长那一格是**上限**：屏上那一行只有那么宽，超了就截给界面看。"""
        assert len(icons._name_from(f"<title>{'浙' * 80}</title>")) == icons._NAME_MAX


class TestTheHop:
    def test_a_meta_refresh_page_is_followed_once(self) -> None:
        """教务那家的根就是一条 refresh：不跟这一跳，屏上永远没有它的名字。"""
        net = FakeNet({
            "https://jw.test/": '<head><meta http-equiv="refresh" content="1; url=/jwglxt"></head>',
            "https://jw.test/jwglxt": "<title>本科教学管理信息服务平台</title>",
        })

        assert icons.site_name("zdbk2", "https://jw.test", opener=net) == "本科教学管理信息服务平台"
        assert len(net.requests) == 2

    def test_a_refresh_to_another_host_is_not_followed(self) -> None:
        """「抓个名字」不许变成"被别人的页面牵着走"：只跟同一个站那一跳。"""
        net = FakeNet({
            "https://a.test/": '<meta http-equiv="refresh" content="0; url=https://evil.test/">',
            "https://evil.test/": "<title>不是它的名字</title>",
        })

        assert icons.site_name("hop", "https://a.test", opener=net) == ""
        assert [u for u in net.requests if "evil.test" in u] == []


class TestTheThreePolitenessRules:
    def test_it_fetches_once_and_the_answer_lives_on_disk(self) -> None:
        net = FakeNet({"https://x.test/": "<title>某站门户</title>"})

        first = icons.site_name("once", "https://x.test", opener=net)
        second = icons.site_name("once", "https://x.test", opener=net)

        assert (first, second) == ("某站门户", "某站门户")
        assert len(net.requests) == 1, "每次开设置页都重打一遍那个站＝把「看一眼界面」变成轮询"
        assert (icons.icon_dir() / "once.name").read_text(encoding="utf-8") == "某站门户"

    def test_a_site_that_reports_nothing_is_marked_as_tried(self) -> None:
        """抓不到也记一笔 miss——否则每次开页都重走一遍超时，那一屏就会转圈。"""
        net = FakeNet({"https://x.test/": "<html><body>没有标题</body></html>"})

        assert icons.site_name("quiet", "https://x.test", opener=net) == ""
        assert (icons.icon_dir() / "quiet.name.miss").exists()
        assert len(net.requests) == 1

    def test_a_dead_site_is_not_an_error_on_the_screen(self) -> None:
        """这一格**永不抛**：名字是装饰性的那一半，站挂了/超时/证书错都只等于"它没报名字"。"""
        def boom(request, timeout=None):
            raise OSError("connection reset")

        assert icons.site_name("dead", "https://down.test", opener=boom) == ""

    def test_a_source_without_a_declared_site_does_not_dial_anything(self) -> None:
        calls: list[str] = []

        def spy(request, timeout=None):
            calls.append("dialed")
            raise AssertionError("不该拨出去")

        assert icons.site_name("local", "", opener=spy) == ""
        assert calls == []

    def test_the_screen_read_stays_non_blocking(self, monkeypatch) -> None:
        """盘上没有 ⇒ 立刻回空串并**排那一趟后台抓取**：这一屏不许为一张名字等 2 秒。"""
        started: list[tuple] = []

        class FakeThread:
            def __init__(self, target, args, daemon=None):
                started.append((target, args))

            def start(self):
                pass

        monkeypatch.setattr("threading.Thread", FakeThread)

        assert icons.site_name_now("bg", "https://x.test") == ""
        assert started and started[0][1] == ("bg", "https://x.test")

    def test_the_cached_answer_short_circuits_the_thread(self) -> None:
        (icons.icon_dir() / "have.name").write_text("已抓好", encoding="utf-8")

        assert icons.site_name_now("have", "https://x.test") == "已抓好"

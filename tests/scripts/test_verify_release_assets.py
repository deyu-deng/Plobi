"""Tests for scripts/plobi/verify_release_assets.py.

A GitHub release page is a one-shot thing: once the tag is published, a
missing installer stays missing. Three accidents came out of that, and each
one of them was invisible at publish time — ``gh release create`` returns 0
for whatever it was handed:

* only the Windows package was uploaded (the other platforms were patched in
  by hand, or never arrived);
* the body was GitHub's auto-generated English commit list instead of the
  hand-written Chinese notes that live in the repo;
* the English attachment was not uploaded, so the body link to it 404s.

So the check has to happen *after* the release exists, and it has to answer
"is this release complete", not "did the publish command succeed".

The module is split in two halves on purpose: the pure half (``expected_assets``
and ``asset_problems``) says what a version must have, and the network half
asks GitHub what it has. Everything below drives the pure half directly, and
the network half through an injected HTTP callable — **no test here touches
the network**.

The fake version is 0.9.9: a version that does not exist, so a test can
never be fooled by a real release.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "plobi" / "verify_release_assets.py"

VERSION = "0.9.9"


def _module():
    spec = importlib.util.spec_from_file_location("verify_release_assets", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_release_assets"] = module
    spec.loader.exec_module(module)
    return module


def _body(version: str = VERSION) -> str:
    """A body shaped like the ones the repo ships."""
    return (
        f"## Plobi v{version}\n\n"
        f"[中文](https://github.com/deyu-deng/Plobi/releases/tag/v{version}) | "
        f"[English](https://github.com/deyu-deng/Plobi/releases/download/"
        f"v{version}/Plobi-{version}-release-notes.en.md)\n"
    )


def _full_assets(module) -> list[str]:
    return module.expected_assets(VERSION)


# ──────────────────────────────────────────────────────────────────────
# 资产名清单的形状
# ──────────────────────────────────────────────────────────────────────


def test_expected_assets_names_every_platform_and_the_english_attachment():
    module = _module()
    assets = module.expected_assets(VERSION)

    assert "Plobi-0.9.9-win-x64.exe" in assets
    assert "Plobi-0.9.9-win-x64.msi" in assets
    assert "Plobi-0.9.9-mac-arm64.dmg" in assets
    assert "Plobi-0.9.9-mac-arm64.zip" in assets
    assert "Plobi-0.9.9-linux-amd64.deb" in assets
    assert "Plobi-0.9.9-linux-x86_64.AppImage" in assets
    assert "Plobi-0.9.9-linux-x86_64.rpm" in assets
    assert "Plobi-0.9.9-release-notes.en.md" in assets


def test_expected_assets_carries_no_build_byproducts():
    """``.blockmap`` 与 ``builder-debug.yml`` 是构建副产物，不是必装资产。

    把它们列进必装清单，就等于要求每次发版都挂上它们 —— 而它们本来就会随
    构建产物一起出现，要求与否都一样；列进去只会让核验在无关的时候红。
    """
    module = _module()
    assets = module.expected_assets(VERSION)

    assert not [name for name in assets if name.endswith(".blockmap")]
    assert "builder-debug.yml" not in assets


def test_expected_assets_takes_a_version_with_or_without_the_v():
    module = _module()
    assert module.expected_assets("v0.9.9") == module.expected_assets("0.9.9")


# ──────────────────────────────────────────────────────────────────────
# 缺项：平台与平台里的单个安装包
# ──────────────────────────────────────────────────────────────────────


def test_a_complete_release_has_no_problems():
    module = _module()
    assert module.asset_problems(_full_assets(module), VERSION, _body()) == []


def test_a_missing_windows_package_is_reported():
    module = _module()
    assets = [name for name in _full_assets(module) if "-win-" not in name]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("Plobi-0.9.9-win-x64.exe" in problem for problem in problems)
    assert any("Plobi-0.9.9-win-x64.msi" in problem for problem in problems)


def test_a_missing_macos_package_is_reported():
    module = _module()
    assets = [name for name in _full_assets(module) if "-mac-" not in name]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("Plobi-0.9.9-mac-arm64.dmg" in problem for problem in problems)
    assert any("Plobi-0.9.9-mac-arm64.zip" in problem for problem in problems)


def test_a_missing_linux_package_is_reported():
    module = _module()
    assets = [name for name in _full_assets(module) if "-linux-" not in name]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("Plobi-0.9.9-linux-amd64.deb" in problem for problem in problems)
    assert any("Plobi-0.9.9-linux-x86_64.AppImage" in problem for problem in problems)
    assert any("Plobi-0.9.9-linux-x86_64.rpm" in problem for problem in problems)


def test_a_missing_english_attachment_is_reported():
    """事故③：正文指着英文附件，而附件没挂上。"""
    module = _module()
    assets = [
        name
        for name in _full_assets(module)
        if not name.endswith("-release-notes.en.md")
    ]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("Plobi-0.9.9-release-notes.en.md" in problem for problem in problems)


def test_one_missing_installer_inside_a_present_platform_is_still_reported():
    """平台在、但少了其中一个安装包 —— 也要报，不能因为「平台齐了」放过。"""
    module = _module()
    assets = [
        name for name in _full_assets(module) if not name.endswith("-mac-arm64.zip")
    ]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("Plobi-0.9.9-mac-arm64.zip" in problem for problem in problems)
    # mac 平台还在（dmg 仍在），所以不该报「整个平台都没有」。
    assert not any("整个平台：mac" in problem for problem in problems)


def test_a_release_with_only_one_platform_is_reported_as_missing_the_others():
    """事故①：只有 win 的包。mac 与 linux 一个都没有，要被点名。"""
    module = _module()
    assets = [name for name in _full_assets(module) if "-win-" in name]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("整个平台：mac" in problem for problem in problems)
    assert any("整个平台：linux" in problem for problem in problems)


def test_byproducts_do_not_fill_a_platform_that_has_no_installer():
    """副产物撑不起一个平台。

    只挂着 win 的安装包与 win 的 blockmap 时，mac 与 linux 仍然是「一个都没有」
    —— 读者在 mac/linux 上一个安装包都下不到。
    """
    module = _module()
    assets = [name for name in _full_assets(module) if "-win-" in name] + [
        "Plobi-0.9.9-win-x64.exe.blockmap",
        "builder-debug.yml",
    ]
    problems = module.asset_problems(assets, VERSION, _body())

    assert any("整个平台：mac" in problem for problem in problems)
    assert any("整个平台：linux" in problem for problem in problems)
    assert not any("整个平台：win" in problem for problem in problems)


def test_byproducts_alone_do_not_raise_a_platform_complaint():
    """只挂副产物时，不报「缺整个平台」。

    那种情况下逐个资产的缺项已经把话说全了，再补三句平台缺失是同一件事说两遍。
    """
    module = _module()
    byproducts = ["Plobi-0.9.9-win-x64.exe.blockmap", "builder-debug.yml"]
    problems = module.asset_problems(byproducts, VERSION, _body())

    assert not [problem for problem in problems if "整个平台" in problem]
    # 必装资产一个都没有，这一条必须报。
    assert any("Plobi-0.9.9-win-x64.exe" in problem for problem in problems)


def test_byproducts_next_to_real_packages_are_not_a_problem():
    """副产物在场本身不是问题 —— 它们本来就随构建产物一起出现。"""
    module = _module()
    assets = _full_assets(module) + [
        "Plobi-0.9.9-win-x64.exe.blockmap",
        "builder-debug.yml",
    ]
    assert module.asset_problems(assets, VERSION, _body()) == []


# ──────────────────────────────────────────────────────────────────────
# 正文核验
# ──────────────────────────────────────────────────────────────────────


def test_a_body_without_the_link_to_its_own_release_page_is_reported():
    """事故②的前半：正文不是仓里那份中文说明（这里缺了自跳链接）。"""
    module = _module()
    body = (
        "[English](https://github.com/deyu-deng/Plobi/releases/download/"
        "v0.9.9/Plobi-0.9.9-release-notes.en.md)\n"
    )
    problems = module.asset_problems(_full_assets(module), VERSION, body)

    assert any("releases/tag/v0.9.9" in problem for problem in problems)


def test_a_body_without_the_link_to_the_english_attachment_is_reported():
    module = _module()
    body = "[中文](https://github.com/deyu-deng/Plobi/releases/tag/v0.9.9)\n"
    problems = module.asset_problems(_full_assets(module), VERSION, body)

    assert any("Plobi-0.9.9-release-notes.en.md" in problem for problem in problems)


def test_an_empty_body_is_reported():
    """正文由仓里的中文说明生成，不该是空的。"""
    module = _module()

    assert module.asset_problems(_full_assets(module), VERSION, "")
    assert module.asset_problems(_full_assets(module), VERSION, None)
    assert module.asset_problems(_full_assets(module), VERSION, "   \n")


# ──────────────────────────────────────────────────────────────────────
# 网络层：注入假的 HTTP，**不发真实请求**
# ──────────────────────────────────────────────────────────────────────


class _FakeHttp:
    """假的 HTTP：按 URL 回答，不出网。"""

    def __init__(self, *, release=None, release_status=200, asset_status=206):
        self.release = release
        self.release_status = release_status
        self.asset_status = asset_status
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url, headers=None, timeout=20.0):
        self.calls.append((url, dict(headers or {}), timeout))
        if "/releases/tags/" in url:
            if self.release is None:
                return self.release_status, b"{}"
            return self.release_status, json.dumps(self.release).encode("utf-8")
        return self.asset_status, b""


def _release_payload(module, *, assets=None, body=None) -> dict:
    names = assets if assets is not None else _full_assets(module)
    return {
        "tag_name": f"v{VERSION}",
        "body": _body() if body is None else body,
        "assets": [
            {"name": name, "url": f"https://example.invalid/assets/{name}"}
            for name in names
        ],
    }


def test_fetch_release_reads_the_release_of_a_tag():
    module = _module()
    payload = _release_payload(module)
    http = _FakeHttp(release=payload)

    assert module.fetch_release(f"v{VERSION}", http_get=http) == payload
    url, headers, _ = http.calls[0]
    assert url.endswith(f"/releases/tags/v{VERSION}")
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["User-Agent"]


def test_fetch_release_sends_the_token_when_there_is_one():
    module = _module()
    http = _FakeHttp(release=_release_payload(module))

    module.fetch_release(f"v{VERSION}", http_get=http, token="t0ken")

    _, headers, _ = http.calls[0]
    assert headers["Authorization"] == "Bearer t0ken"


def test_fetch_release_raises_when_the_release_cannot_be_read():
    """匿名请求看不到私有仓时 GitHub 答 404 —— 那不是「发布有问题」，是「没核成」。"""
    module = _module()
    http = _FakeHttp(release=None, release_status=404)

    with pytest.raises(module.ReleaseUnreachable):
        module.fetch_release(f"v{VERSION}", http_get=http)


def test_verify_release_reports_a_missing_asset():
    module = _module()
    payload = _release_payload(
        module, assets=[n for n in _full_assets(module) if "-linux-" not in n]
    )
    http = _FakeHttp(release=payload)

    problems = module.verify_release(f"v{VERSION}", http_get=http)

    assert any("Plobi-0.9.9-linux-amd64.deb" in problem for problem in problems)


def test_verify_release_reports_an_asset_that_cannot_be_downloaded():
    """挂了名字但下不来，等于没挂。"""
    module = _module()
    payload = _release_payload(module)
    http = _FakeHttp(release=payload, asset_status=404)

    problems = module.verify_release(f"v{VERSION}", http_get=http)

    assert any("下载不了" in problem for problem in problems)


def test_verify_release_accepts_a_complete_release():
    module = _module()
    payload = _release_payload(module)
    http = _FakeHttp(release=payload)

    assert module.verify_release(f"v{VERSION}", http_get=http) == []


def test_the_asset_probe_asks_for_one_byte():
    """探测用 Range，只取一个字节 —— 不把整个安装包拉下来。"""
    module = _module()
    assets = [{"name": "a.exe", "url": "https://example.invalid/assets/a.exe"}]
    http = _FakeHttp()

    assert module.asset_download_problems(assets, http_get=http) == []

    url, headers, _ = http.calls[0]
    assert url == "https://example.invalid/assets/a.exe"
    assert headers["Range"] == "bytes=0-0"
    assert headers["Accept"] == "application/octet-stream"


def test_a_timeout_is_retried_once():
    """连接超时重试一次：网络抖一下不等于资产坏了。"""

    module = _module()
    attempts = {"count": 0}

    def flaky(url, headers=None, timeout=20.0):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("timed out")
        return 206, b""

    assets = [{"name": "a.exe", "url": "https://example.invalid/assets/a.exe"}]
    assert module.asset_download_problems(assets, http_get=flaky) == []
    assert attempts["count"] == 2


def test_a_persistently_timing_out_asset_is_a_problem():
    module = _module()

    def always_timeout(url, headers=None, timeout=20.0):
        raise TimeoutError("timed out")

    assets = [{"name": "a.exe", "url": "https://example.invalid/assets/a.exe"}]
    problems = module.asset_download_problems(assets, http_get=always_timeout)

    assert problems
    assert "a.exe" in problems[0]


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def test_the_cli_reports_problems_and_exits_non_zero(capsys):
    module = _module()
    payload = _release_payload(
        module, assets=[n for n in _full_assets(module) if "-mac-" not in n]
    )
    http = _FakeHttp(release=payload)

    code = module.main(["--tag", f"v{VERSION}", "--json"], http_get=http, token=None)

    assert code == module.EXIT_PROBLEMS
    out = json.loads(capsys.readouterr().out)
    assert out["tag"] == f"v{VERSION}"
    assert out["ok"] is False
    assert out["problems"]


def test_the_cli_json_shape(capsys):
    module = _module()
    http = _FakeHttp(release=_release_payload(module))

    code = module.main(["--tag", f"v{VERSION}", "--json"], http_get=http, token=None)

    assert code == module.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out == {"tag": f"v{VERSION}", "ok": True, "problems": []}


def test_the_cli_exits_two_when_the_release_cannot_be_read(capsys):
    module = _module()
    http = _FakeHttp(release=None, release_status=404)

    code = module.main(["--tag", f"v{VERSION}", "--json"], http_get=http, token=None)

    assert code == module.EXIT_UNREACHABLE
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_the_offline_mode_never_touches_the_network(capsys):
    """``--offline`` 只跑纯逻辑：喂进去的资产名是本地的，不是 GitHub 的。"""
    module = _module()

    def explode(*args, **kwargs):  # pragma: no cover - 走不到就是过
        raise AssertionError("offline mode must not make a request")

    code = module.main(
        ["--tag", f"v{VERSION}", "--offline", "--json"],
        http_get=explode,
        token=None,
    )

    out = json.loads(capsys.readouterr().out)
    assert out["tag"] == f"v{VERSION}"
    assert code in (module.EXIT_OK, module.EXIT_PROBLEMS)


def test_the_injected_http_may_return_a_plain_tuple():
    """CI 接自己的客户端时，返回 ``(状态码, 字节)`` 就够，不必import这个模块。

    注入的缝只有一个：``http_get(url, headers, timeout)``。
    """
    module = _module()

    def tuple_http(url, headers=None, timeout=20.0):
        return 200, json.dumps(_release_payload(module)).encode("utf-8")

    assert module.fetch_release(f"v{VERSION}", http_get=tuple_http)

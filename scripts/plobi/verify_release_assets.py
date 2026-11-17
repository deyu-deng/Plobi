#!/usr/bin/env python3
"""发版之后回头核一遍：这一版的发布页该有的东西是不是都在。

为什么要有这个：发布页是**一次性**的东西——发出去之后，缺的东西不会自己长出来，
而过去三类事故都不会自己报错：

① **只有 win 的包**（mac/linux 事后手工补传，或者干脆没传）；
② **正文是 GitHub 按提交标题自动凑的英文清单**，不是仓里那份手写中文；
③ **英文附件没挂上**，正文里指向它的那条链接 404。

发版脚本自己看不出这三条：``gh release create`` 给什么就发什么，退出码 0 只说明
「发布成功了」，不说明「发布齐了」。所以本脚本是**发完之后**回头问 GitHub 一句：
这个 tag 的 release 上到底有什么。

用法：
    python scripts/plobi/verify_release_assets.py --tag v0.3.3            # 联网核
    python scripts/plobi/verify_release_assets.py --tag v0.3.3 --json     # 机器读
    python scripts/plobi/verify_release_assets.py --tag v0.3.3 --offline   # 不联网

退出码：
    0  齐
    1  有缺项/不对项（逐条打印）
    2  读不到那个 Release（tag 不存在、token 不足、网络不通）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
REPO = "deyu-deng/Plobi"
API_RELEASE = "https://api.github.com/repos/{repo}/releases/tags/{tag}"
USER_AGENT = "plobi-release-verify"

# electron-builder 的产物名模板是 ``Plobi-${version}-${os}-${arch}.${ext}``，
# 但 deb/rpm 落在 ``amd64``、AppImage 落在 ``x86_64`` —— 名字不是我们自己编的，
# 照着构建器的实际产物写死，改模板时这里要跟着改。
PLATFORM_INSTALLERS: dict[str, tuple[str, ...]] = {
    "win": ("Plobi-{version}-win-x64.exe", "Plobi-{version}-win-x64.msi"),
    "mac": ("Plobi-{version}-mac-arm64.dmg", "Plobi-{version}-mac-arm64.zip"),
    "linux": (
        "Plobi-{version}-linux-amd64.deb",
        "Plobi-{version}-linux-x86_64.AppImage",
        "Plobi-{version}-linux-x86_64.rpm",
    ),
}
ENGLISH_NOTES = "Plobi-{version}-release-notes.en.md"


EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_UNREACHABLE = 2

# 「没给 token」与「显式给一个空 token」不是一回事：调用方显式传 token=None
# 时不许再去读环境变量。这个哨兵把两者分开。
_FROM_ENV = object()


@dataclass(frozen=True)
class HttpResponse:
    """一次 HTTP 回答。注入的 HTTP 函数要返回的就是它。"""

    status: int
    body: bytes = b""


# 可注入的 HTTP 缝：签名固定为 ``http_get(url, headers, timeout)``。
# 返回 ``HttpResponse`` 或 ``(状态码, 字节)`` 都收（见 as_http_response）。
# CI 里要接自己的客户端，就照这个签名做一个函数塞进来。
HttpGetter = Callable[[str, "Mapping[str, str] | None", float], Any]


class ReleaseUnreachable(Exception):
    """读不到那个 Release —— 不是「有问题」，是「没核成」。"""


class _CrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """跨主机跳转时丢掉 Authorization。

    GitHub 的资产接口会把我们**重定向**到对象存储/CDN（另一个主机）。
    跟着跳过去还带着 token，等于把凭据递给了第三方，对象存储也会因为
    「一套 URL 签名 + 一套 Authorization」直接报错。两头都坏，所以这里剥掉。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return new
        if _host(newurl) != _host(req.full_url):
            new.remove_header("Authorization")
        return new


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def fetch_url(
    url: str,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20.0,
) -> HttpResponse:
    """默认的 HTTP 实现：标准库 urllib，不引第三方。

    代理被显式关掉（``ProxyHandler({})``）——一是别把 token 递给代理，
    二是仓库里另一个读 GitHub 的脚本（``check_magpie_release.py``）就是这么走的。
    """
    request = urllib.request.Request(url, headers=dict(headers or {}))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _CrossHostRedirect(),
    )
    with opener.open(request, timeout=timeout) as response:
        return HttpResponse(status=int(response.status), body=response.read())


def as_http_response(value: Any) -> HttpResponse:
    """把注入方给的东西收成 ``HttpResponse``。

    CI 那边接自己的 HTTP 客户端时，最省事的是直接返回 ``(状态码, 字节)``；
    返回 ``HttpResponse`` 也行。两种都收下，接线的人就不必import这个模块的
    数据类才能用。
    """
    if isinstance(value, HttpResponse):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        status, body = value
        if isinstance(body, str):
            body = body.encode("utf-8")
        return HttpResponse(status=int(status), body=bytes(body or b""))
    raise TypeError(f"cannot read an HTTP answer out of {type(value).__name__}")


# ──────────────────────────────────────────────────────────────────────
# 纯逻辑：这一版该有什么
# ──────────────────────────────────────────────────────────────────────


def normalize_version(version: str) -> str:
    """``v0.3.3`` 与 ``0.3.3`` 归一成同一个写法。"""
    text = str(version or "").strip()
    if text.startswith("v"):
        text = text[1:]
    return text


def platform_installers(version: str) -> dict[str, list[str]]:
    """各平台的安装包名字（这一版）。"""
    v = normalize_version(version)
    return {
        platform: [template.format(version=v) for template in templates]
        for platform, templates in PLATFORM_INSTALLERS.items()
    }


def english_notes_name(version: str) -> str:
    """英文发布说明作为附件的名字 —— 正文里的链接就指着它。"""
    return ENGLISH_NOTES.format(version=normalize_version(version))


def expected_assets(version: str) -> list[str]:
    """这一版**必须**存在的资产名清单。

    三个平台各一份安装包 + 一份英文发布说明。``.blockmap`` 与
    ``builder-debug.yml`` 是构建副产物，不在清单里（它们在不在都不影响发布）。
    """
    v = normalize_version(version)
    installers = platform_installers(v)
    out: list[str] = []
    for platform in ("win", "mac", "linux"):
        out.extend(installers[platform])
    out.append(english_notes_name(v))
    return out


def platform_present(assets: list[str], version: str, platform: str) -> bool:
    """这个平台上有没有**这一版的安装包**。

    只认该平台的**必装资产**（安装包本体）。`.blockmap` 与 `builder-debug.yml`
    是构建副产物，它们撑不起一个平台 —— 一份只挂着 win 的 blockmap 的发布页，
    对只拿到 Windows 的读者来说仍然是「没有 mac/linux」。
    """
    installers = platform_installers(version)[platform]
    names = {str(name) for name in assets}
    return any(name in names for name in installers)


def platform_problems(assets: list[str], version: str) -> list[str]:
    """平台级缺项：这一版不能只有 win。

    判据是「该平台的安装包一个都没挂上」，副产物不算平台在场。

    一个平台的安装包都没挂上时，这里不报平台缺失 —— 那种情况下逐个资产的
    缺项已经把话说尽了，再补三句「缺整个平台」是同一件事说两遍。
    """
    names = {str(name) for name in assets}
    shipped = {
        platform: platform_present(list(names), version, platform)
        for platform in ("win", "mac", "linux")
    }
    if not any(shipped.values()):
        return []
    return [
        f"缺少整个平台：{platform}（这一版没有任何 {platform} 的安装包）"
        for platform in ("win", "mac", "linux")
        if not shipped[platform]
    ]


def body_problems(body: str | None, version: str) -> list[str]:
    """正文里的问题：必须能跳回自己的发布页，也必须指着英文附件。

    正文由仓里的中文说明生成（``docs/plobi/releases/vX.md``），所以它为空
    就说明发版时落进了 ``--generate-notes`` 的回退分支 —— 那是事故②。
    """
    v = normalize_version(version)
    text = body or ""
    if not text.strip():
        return ["正文是空的：这一版的发布说明由仓里的中文说明生成，不该为空"]
    problems: list[str] = []
    tag_link = f"releases/tag/v{v}"
    if tag_link not in text:
        problems.append(f"正文里没有指向自己发布页的链接（{tag_link}）")
    english = english_notes_name(v)
    if english not in text:
        problems.append(f"正文里没有指向英文附件的链接（{english}）")
    return problems


def asset_problems(
    assets: list[str],
    version: str,
    body: str | None = None,
) -> list[str]:
    """实际资产名清单 + 正文 ⇒ 人话问题清单（空清单 = 齐了）。

    三层检查：逐个必需资产在不在 → 每个平台在不在 → 正文的两条互跳链接在不在。
    """
    v = normalize_version(version)
    names = {str(name) for name in assets}
    problems: list[str] = []

    installers = platform_installers(v)
    for platform in ("win", "mac", "linux"):
        for name in installers[platform]:
            if name not in names:
                problems.append(f"缺少资产：{name}")
    english = english_notes_name(v)
    if english not in names:
        problems.append(f"缺少英文发布说明附件：{english}")

    problems.extend(platform_problems(list(names), v))
    problems.extend(body_problems(body, v))
    return problems


# ──────────────────────────────────────────────────────────────────────
# 网络层
# ──────────────────────────────────────────────────────────────────────


def fetch_release(
    tag: str,
    *,
    http_get: HttpGetter = fetch_url,
    token: str | None = None,
    timeout: float = 20.0,
    repo: str = REPO,
) -> dict[str, Any]:
    """问 GitHub 要那个 tag 的 Release。

    拿不到（tag 不存在 / 匿名看不到私有仓 / 网络不通）抛 ``ReleaseUnreachable``：
    这跟「发布有问题」是两回事，退出码要分开（2 vs 1）。
    """
    url = API_RELEASE.format(repo=repo, tag=tag)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = as_http_response(http_get(url, headers, timeout))
    except urllib.error.HTTPError as exc:
        raise ReleaseUnreachable(
            f"GitHub 回了 {exc.code}（{url}）：tag 不存在，或这个仓不是公开的而 token 不足"
        ) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ReleaseUnreachable(f"连不上 GitHub（{url}）：{exc}") from exc
    if response.status != 200:
        raise ReleaseUnreachable(f"GitHub 回了 {response.status}（{url}）")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReleaseUnreachable(f"GitHub 回了看不懂的内容：{exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseUnreachable("GitHub 回了看不懂的内容")
    return payload


def _probe(
    http_get: HttpGetter,
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int | None, str]:
    """打一次 Range 探测，返回 (状态码, 失败原因)。

    超时值得重试一次（网络抖一下不等于资产坏了）；别的错重试也白搭。
    """
    last = ""
    for _ in range(2):
        try:
            response = as_http_response(http_get(url, headers, timeout))
        except urllib.error.HTTPError as exc:
            return int(exc.code), f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                last = f"超时：{reason}"
                continue
            return None, f"连接失败：{exc}"
        except TimeoutError as exc:
            last = f"超时：{exc}"
            continue
        except OSError as exc:
            return None, f"连接失败：{exc}"
        else:
            if response.status == 206:
                return 206, ""
            return response.status, f"期望 206（Range 探测），实际 {response.status}"
    return None, last


def asset_download_problems(
    assets: list[dict[str, Any]],
    *,
    http_get: HttpGetter = fetch_url,
    token: str | None = None,
    timeout: float = 20.0,
) -> list[str]:
    """每个资产都要真能下载 —— 挂了名字但下不来，等于没挂。

    探测方式：对 ``asset['url']`` 发 ``Range: bytes=0-0`` 的 GET，
    期望 ``206``（只取一个字节，不把整个安装包拉下来）。
    """
    problems: list[str] = []
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("url") or "")
        if not url:
            problems.append(f"资产 {name} 没有下载地址")
            continue
        headers = {
            "Accept": "application/octet-stream",
            "User-Agent": USER_AGENT,
            "Range": "bytes=0-0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        status, reason = _probe(http_get, url, headers, timeout)
        if status == 206:
            continue
        problems.append(f"资产 {name} 下载不了（{url}）：{reason}")
    return problems


def verify_release(
    tag: str,
    *,
    http_get: HttpGetter = fetch_url,
    token: str | None = None,
    timeout: float = 20.0,
    repo: str = REPO,
) -> list[str]:
    """核这个 tag 的发布页，返回问题清单（空 = 齐了）。"""
    payload = fetch_release(
        tag, http_get=http_get, token=token, timeout=timeout, repo=repo
    )
    raw_assets = [a for a in (payload.get("assets") or []) if isinstance(a, dict)]
    names = [str(a.get("name") or "") for a in raw_assets]
    version = normalize_version(str(payload.get("tag_name") or tag))
    body = payload.get("body")
    problems = asset_problems(names, version, body if isinstance(body, str) else None)
    problems.extend(
        asset_download_problems(
            raw_assets, http_get=http_get, token=token, timeout=timeout
        )
    )
    return problems


# ──────────────────────────────────────────────────────────────────────
# 离线口径：不联网时，核「现在发出去会有哪些资产」
# ──────────────────────────────────────────────────────────────────────


def local_asset_names(version: str) -> list[str]:
    """本机构建产物里的资产名。

    electron-builder 的输出目录（``apps/desktop/release``）里躺着这一版构建出来的
    安装包与副产物；英文发布说明住在仓里（``docs/plobi/releases``），发版时会被
    挂成附件。两者合起来就是「现在发出去，GitHub 上会有哪些名字」。
    """
    names: list[str] = []
    build_dir = ROOT / "apps" / "desktop" / "release"
    if build_dir.is_dir():
        for entry in sorted(build_dir.iterdir()):
            if entry.is_file():
                names.append(entry.name)
    english = ROOT / "docs" / "plobi" / "releases" / english_notes_name(version)
    if english.is_file():
        names.append(english.name)
    return names


def local_body(version: str) -> str | None:
    """仓里那份中文正文（发版时 ``--notes-file`` 用的就是它）。"""
    path = ROOT / "docs" / "plobi" / "releases" / f"v{normalize_version(version)}.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def _read_token() -> str | None:
    """token 只从环境变量来，代码里不许出现任何密钥字面量。"""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def main(
    argv: list[str] | None = None,
    *,
    http_get: HttpGetter | None = None,
    token: Any = _FROM_ENV,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="要核的发布 tag，例如 v0.3.3")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="不联网：只核本机构建产物与仓里的正文",
    )
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    version = normalize_version(args.tag)
    getter: HttpGetter = http_get or fetch_url
    auth = _read_token() if token is _FROM_ENV else token

    if args.offline:
        problems = asset_problems(
            local_asset_names(version), version, local_body(version)
        )
        if args.json:
            print(
                json.dumps(
                    {"tag": args.tag, "ok": not problems, "problems": problems},
                    ensure_ascii=False,
                )
            )
        elif problems:
            print(f"✗ {args.tag} 离线核验有 {len(problems)} 处不对：")
            for problem in problems:
                print(f"  · {problem}")
        else:
            print(
                f"✔ {args.tag} 本地产物齐了（{len(expected_assets(version))} 个必需资产都在）。"
            )
        return EXIT_PROBLEMS if problems else EXIT_OK

    try:
        problems = verify_release(args.tag, http_get=getter, token=auth)
    except ReleaseUnreachable as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "tag": args.tag,
                        "ok": False,
                        "problems": [f"拿不到这个 Release：{exc}"],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"✗ 拿不到这个 Release：{exc}", file=sys.stderr)
        return EXIT_UNREACHABLE

    if args.json:
        print(
            json.dumps(
                {"tag": args.tag, "ok": not problems, "problems": problems},
                ensure_ascii=False,
            )
        )
    elif problems:
        print(f"✗ {args.tag} 的发布页有 {len(problems)} 处不对：")
        for problem in problems:
            print(f"  · {problem}")
    else:
        print(
            f"✔ {args.tag} 的发布页齐了："
            f"{len(expected_assets(version))} 个必需资产都在，正文的两条链接也在。"
        )
    return EXIT_PROBLEMS if problems else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

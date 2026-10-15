"""The desktop's own traffic, and the files the engine needs to start at all (WP-EGRESS-ROUTING).

口径 32 (整块交付、每格测试≤2) — so this file says two things, each covering a whole surface:

* **③ 桌面自己那三档**: ``session.setProxy`` does not execute our WinINET PAC, so Electron's traffic
  followed whatever the machine's settings said — the app could be "taken over" while its own window
  still went out the old way. The answer comes from the same table in Chromium's dialect, which is
  *richer* than ``ProxyOverride`` (CIDR and IPv6 are expressible), so nothing is narrowed on the way.

* **① 随包分发**: measured on this box — an engine workdir without ``GeoSite.dat`` starts, prints
  ``Can't find GeoSite.dat, start download`` and never binds, and that download needs the egress
  the engine is supposed to provide (纸 §28). An installed app must not need anyone to copy files
  by hand, so the copy happens here, from the bundle, and never from the network.
"""

from __future__ import annotations

from agent.egress import engine_files, system_proxy
from agent.egress.rules import parse_table


def _table():
    return parse_table({
        "proxies": {"home": {"url": "http://127.0.0.1:7890"}},
        "rules": [
            {"match": "github.com", "via": "direct"},
            {"match": "*.corp.example", "via": "direct"},
            {"match": "10.0.0.0/8", "via": "direct"},
            {"match": "geosite:streaming", "via": "direct"},
            {"match": "*", "via": "proxy:home"},
        ],
        "engine": {"enabled": True, "mixed_port": 7895},
        "system_proxy": {"enabled": True},
    })


def test_the_desktop_gets_the_table_in_chromiums_dialect_and_system_when_not_taken_over():
    """三档共用一个答案：接管了就 fixed_servers，没接管/没端口就分别回到 system / off。"""
    view = system_proxy.desktop_view(_table())

    assert view["declared"] is True and view["mode"] == "engine", view
    assert view["proxy"] == "http://127.0.0.1:7895"
    # the CIDR WinINET's ProxyOverride cannot express is exactly what Chromium can — it must survive
    assert "10.0.0.0/8" in view["bypass"], view["bypass"]
    assert {"github.com", "*.corp.example", "127.0.0.1"} <= set(view["bypass"]), view["bypass"]
    # a `geosite:` classification is answerable only by the engine: named, never silently dropped
    assert view["unexpressible"] == ["geosite:streaming"], view
    assert "引擎" in view["note"], view["note"]

    quiet = system_proxy.desktop_view(parse_table({"rules": [{"match": "*", "via": "direct"}]}))

    assert quiet["mode"] == "system" and quiet["proxy"] == "", "没接管就不许替用户改他机器的走法"

    no_port = system_proxy.desktop_view(
        parse_table({"rules": [{"match": "*", "via": "direct"}], "system_proxy": {"enabled": True}})
    )

    assert no_port["mode"] == "off" and "mixed_port" in no_port["note"], no_port


def test_a_packaged_install_seeds_the_engine_from_the_bundle_and_a_source_checkout_does_not(tmp_path, monkeypatch):
    """① 一条路走完：随包赢过家目录、起之前真复制、警告从"缺"→"会播"→没有、二次不覆盖；没有随包就照今天。"""
    bundle = tmp_path / "resources" / "egress"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "bin" / "mihomo.exe").write_bytes(b"b" * 2048)
    (bundle / "GeoSite.dat").write_bytes(b"g" * 512)
    (bundle / "geoip.metadb").write_bytes(b"i" * 700)
    home = tmp_path / "plobi-home"
    work = home / "egress" / "runtime"
    work.mkdir(parents=True)

    monkeypatch.setenv("PLOBI_EGRESS_ASSET_DIR", str(bundle))

    assert engine_files.bundle_dir() == bundle
    assert engine_files.default_binary(home=home) == str(bundle / "bin" / "mihomo.exe"), "随包必须赢过家目录"
    before = engine_files.geo_warnings(work)

    assert len(before) == 2 and all("会在起之前" in line for line in before), before

    placed = engine_files.seed_into(work, home=home)

    assert len(placed) == 2 and "512" in "".join(placed), f"报真实字节数而不是清单标称: {placed}"
    assert (work / "GeoSite.dat").read_bytes() == b"g" * 512
    assert (work / "geoip.metadb").stat().st_size == 700
    assert engine_files.geo_warnings(work) == [], engine_files.geo_warnings(work)
    assert engine_files.seed_into(work, home=home) == [], "第二次不许覆盖已经在那一份"

    monkeypatch.delenv("PLOBI_EGRESS_ASSET_DIR")

    assert engine_files.bundle_dir() is None
    assert engine_files.default_binary(home=home) == "", "没带就是没带，不许指向一个不存在的路径当作有"
    assert engine_files.artifact_path(engine_files.GEOSITE, home=home) == home / "egress" / "GeoSite.dat"

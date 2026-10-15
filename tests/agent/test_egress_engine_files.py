"""The shipped engine files' runtime half (WP-EGRESS-ROUTING §28).

随包分发 lands these on the `$PLOBI_HOME` side, never in the repo (裁定 134: Code is a public
product repo).  Nothing here downloads anything — the pinned digests are read from what this
box actually runs, and the value of the module is that a missing file becomes a sentence
instead of a hung engine.
"""

from __future__ import annotations

import hashlib
import re

from agent.egress import engine, engine_files
from agent.egress.engine_files import ARTIFACTS, BINARY, GEOIP, GEOSITE
from agent.egress.rules import load_table

_TABLE = {
    "proxies": {"gw": {"url": "http://127.0.0.1:7898"}},
    "rules": [{"match": "*", "via": "proxy:gw"}],
    "engine": {"enabled": True, "mixed_port": 7895},
}


def _table(**engine_extra):
    return load_table(
        config={"network": {"egress": {**_TABLE, "engine": {**_TABLE["engine"], **engine_extra}}}},
        force_reload=True,
    )


def test_the_manifest_pins_measured_values_not_invented_ones():
    for a in ARTIFACTS:
        assert re.fullmatch(r"[0-9a-f]{64}", a.sha256), a
        assert a.bytes > 0, a
    assert engine_files.ENGINE_VERSION.startswith("v")


def test_a_missing_geo_file_is_named_with_the_path_it_wants(tmp_path):
    lines = engine_files.geo_warnings(tmp_path)

    assert len(lines) == 2, lines
    joined = " ".join(lines)
    assert "GeoSite.dat" in joined and "geoip.metadb" in joined
    #: the whole point: the deadlock is named, including who provides the download's egress
    assert "never bind" in joined and str(tmp_path) in joined


def test_geo_data_sitting_in_the_workdir_says_nothing(tmp_path):
    (tmp_path / "GeoSite.dat").write_bytes(b"x")
    (tmp_path / "geoip.metadb").write_bytes(b"y")

    assert engine_files.geo_warnings(tmp_path) == []


def test_locate_does_not_hash_until_asked(tmp_path):
    f = tmp_path / "GeoSite.dat"
    f.write_bytes(b"hello")

    lazy = engine_files.locate(GEOSITE, workdir=tmp_path)
    checked = engine_files.locate(GEOSITE, workdir=tmp_path, verify=True)

    assert lazy["exists"] and lazy["sha256"] == "", lazy
    assert checked["sha256"] == hashlib.sha256(b"hello").hexdigest(), checked


def test_the_shipped_binary_is_found_only_where_it_should_be(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_files, "egress_root", lambda home=None: tmp_path)

    assert engine_files.default_binary() == ""
    placed = tmp_path / BINARY.subdir / BINARY.basename
    placed.parent.mkdir(parents=True)
    placed.write_bytes(b"bin")

    assert engine_files.default_binary() == str(placed)


def test_snapshot_falls_back_to_the_shipped_engine_when_the_table_names_none(tmp_path, monkeypatch):
    placed = tmp_path / "bin" / "mihomo.exe"
    placed.parent.mkdir(parents=True)
    placed.write_bytes(b"bin")
    monkeypatch.setattr(engine_files, "artifact_path",
                        lambda a, home=None, workdir=None: placed if a is BINARY else tmp_path / a.basename)

    snap = engine.snapshot(_table(), home=tmp_path / "home")

    assert snap.binary == str(placed), snap
    assert any("using the shipped engine" in n for n in snap.notes), snap.notes


def test_the_files_note_counts_a_binary_the_table_points_at_somewhere_else(tmp_path, monkeypatch):
    """The count must answer "is there an engine to run", not "is it in our folder"."""
    elsewhere = tmp_path / "portable" / "mihomo.exe"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"bin")
    (tmp_path / "home" / "egress").mkdir(parents=True)
    egress = tmp_path / "home" / "egress"
    (egress / "GeoSite.dat").write_bytes(b"g")
    (egress / "geoip.metadb").write_bytes(b"m")
    monkeypatch.setattr(engine_files, "artifact_path",
                        lambda a, home=None, workdir=None: egress / a.basename)

    snap = engine.snapshot(_table(binary=str(elsewhere)), home=tmp_path / "home")
    said = " ".join(n for n in snap.notes if "engine files" in n)

    assert "3/3 在位" in said, snap.notes
    assert "binary 在" in said, snap.notes

"""Tests for plugins/plobi-north-star (deep façade + single plobi tool)."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _plugin_dir() -> Path:
    return _repo_root() / "plugins" / "plobi-north-star"


def _load_plugin():
    name = "plobi_plugins.plobi_north_star"
    if name in sys.modules and hasattr(sys.modules[name], "register"):
        return sys.modules[name]
    if "plobi_plugins" not in sys.modules:
        ns = types.ModuleType("plobi_plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        sys.modules["plobi_plugins"] = ns
    plugin_dir = _plugin_dir()
    spec = importlib.util.spec_from_file_location(
        name,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = name
    mod.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "plobi"
    home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("PLOBI_HID_MOCK", "1")
    return home


@pytest.fixture()
def ns(tmp_home):
    mod = _load_plugin()
    import plobi_plugins.plobi_north_star.lib.facade as facade  # type: ignore

    facade._SERVICE = None
    return mod


def test_public_export_is_narrow(ns):
    from plobi_plugins.plobi_north_star.lib import __all__ as public

    assert set(public) == {"NorthStar", "get_north_star"}


def test_deep_tool_task_and_compute(ns, tmp_home):
    from plobi_plugins.plobi_north_star import tools as T

    enq = json.loads(
        T.plobi(
            {
                "area": "task",
                "action": "enqueue",
                "goal": "scan news",
                "risk": "L0",
                "mirror_kanban": False,
            }
        )
    )
    assert enq["ok"] is True
    route = json.loads(T.plobi({"area": "compute", "action": "route", "surface": "marvis"}))
    assert route["path"] == "hid"
    hid = json.loads(
        T.plobi(
            {
                "area": "compute",
                "action": "hid_run",
                "surface": "marvis",
                "prompt": "ping",
                "mock": True,
            }
        )
    )
    assert hid["ok"] is True


def test_stage_gate_via_facade(ns, tmp_home):
    from plobi_plugins.plobi_north_star.lib.facade import get_north_star

    svc = get_north_star()
    enq = svc.task("enqueue", goal="ship", risk="L1", domain="code", mirror_kanban=False)
    tid = enq["task"]["id"]
    svc.task("stage_advance", task_id=tid)  # intake → sketch
    paused = svc.task("stage_advance", task_id=tid)  # sketch gate
    assert paused.get("paused") or paused.get("awaiting_human")
    ok = svc.task("stage_approve", task_id=tid)
    assert ok.get("ok") is True


def test_night_blocks_high_risk(ns, tmp_home):
    from plobi_plugins.plobi_north_star.lib.facade import get_north_star
    from plobi_plugins.plobi_north_star.lib.queue import TaskStatus

    svc = get_north_star()
    high = svc.task("enqueue", goal="publish", risk="L3", mirror_kanban=False)
    assert high["task"]["status"] == TaskStatus.BLOCKED_AWAITING_HUMAN.value
    svc.task("enqueue", goal="observe", risk="L0", mirror_kanban=False)
    tick = svc.ops("night_tick")
    assert tick["claimed"] is not None
    assert tick["claimed"]["goal"] == "observe"


def test_preview_priority(ns, tmp_home):
    from plobi_plugins.plobi_north_star.lib.facade import get_north_star

    svc = get_north_star()
    svc.preview("push", title="res", priority="resource")
    svc.preview("push", title="art", priority="artifact")
    items = svc.preview("list")["items"]
    assert items[0]["title"] == "art"


def test_learn_and_diagnose(ns, tmp_home):
    from plobi_plugins.plobi_north_star.lib.facade import get_north_star

    svc = get_north_star()
    for _ in range(3):
        out = svc.ops("learn_observe", title="rename", steps=["a", "b"])
    assert out.get("draft")
    report = svc.ops("diagnose")
    assert "findings" in report


def test_gateway_hook_points_at_deep_tool(ns):
    class E:
        text = "/plobi board"

    out = ns._on_pre_gateway_dispatch(event=E())
    assert out["action"] == "rewrite"
    assert "area=ops" in out["text"] or "plobi" in out["text"]


def test_register_deep_tool_and_secretary_surface(ns):
    registered = []

    class Ctx:
        def register_tool(self, **kwargs):
            registered.append(kwargs["name"])

        def register_hook(self, *a, **k):
            return None

    ns.register(Ctx())
    assert registered[0] == "plobi"
    assert registered[1:] == [
        "plobi_master_status",
        "plobi_master_preview",
        "plobi_master_dispatch",
        "plobi_master_approve",
        "plobi_secretary_ask",
    ]


def test_boot_get_north_star(tmp_home):
    sys.path.insert(0, str(_plugin_dir() / "lib"))
    import boot

    svc = boot.get_north_star()
    assert svc.task("board")

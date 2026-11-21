"""``Unknown provider`` 文案的守卫：**永远念软件名，绝不念机器标识**。

主人 11-19 的原话：「provider 里面也不要出现 bezel。要报错也是
``Unknown provider '具体的软件名称'. Check 'plobi model' for available
providers, or run 'plobi doctor' to diagnose config issues.``」

背景：``bezel`` 是网关贴在 loopback 那条腿上的**传输名**（现役，唯一
``providers/hub_software.TRANSPORT_NAME``），不是 provider，报错里念出来用户会以为
「bezel 是个 provider」，而 picker 里根本没有这一行。更早两代机器标识（``aigw``／
``quota_hub``）11-19 起已彻底剔除，代码里零痕迹：它们既不是 provider 也不是传输名，
配置里碰上就是普通的不认识，报错照原样念——没有闭嘴名单那种半条兼容路径。

这个文件锁两件事：

1. ``plobi_cli.models`` 的文案工厂：现役传输名换软件名，换不出来连名字都不念；
2. 源码级守卫：``Unknown provider`` 文案不许直接插裸变量，退役标识不许回到代码里。
"""

from __future__ import annotations

import io
import os
import pathlib

import pytest

from plobi_cli.models import (
    format_unknown_provider,
    provider_name_for_message,
    unknown_provider_label,
)
from providers.hub_software import hub_group

MACHINE_IDS = ("bezel",)

FULL_HINT = (
    " Check 'plobi model' for available providers, or run 'plobi doctor' "
    "to diagnose config issues."
)


@pytest.fixture
def config_with_hub_default(monkeypatch):
    """存量配置：provider 还是机器标识，模型 id 带软件前缀。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda *a, **k: {"model": {"provider": "bezel", "default": "workbuddy/hy4free"}},
    )
    monkeypatch.delenv("PLOBI_MODEL", raising=False)
    return monkeypatch


@pytest.fixture
def config_without_usable_default(monkeypatch):
    """推不出软件名的配置：模型 id 不带前缀。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda *a, **k: {"model": {"provider": "bezel", "default": "hy4free"}},
    )
    monkeypatch.delenv("PLOBI_MODEL", raising=False)
    return monkeypatch


def test_software_prefix_is_read_off_the_model_id():
    assert hub_group().member_for_model("workbuddy/hy4free") == "workbuddy"
    assert hub_group().member_for_model("opencode-zen-free/mimo-v2.6-flash-free") == (
        "opencode-zen-free"
    )
    assert hub_group().member_for_model("hy4free") is None
    assert hub_group().member_for_model("") is None


@pytest.mark.parametrize("machine", MACHINE_IDS)
def test_machine_id_is_never_spoken(config_with_hub_default, machine):
    """机器标识 ⇒ 念出``model.default`` 那家软件，一个字母都不许漏。"""
    assert provider_name_for_message(machine) == "workbuddy"

    text = format_unknown_provider(machine)
    assert text == f"Unknown provider 'workbuddy'.{FULL_HINT}"
    for leaked in MACHINE_IDS:
        assert leaked not in text.lower()


@pytest.mark.parametrize("machine", MACHINE_IDS)
def test_machine_id_without_a_usable_prefix_drops_the_name(
    config_without_usable_default, machine
):
    """换不出软件名就干脆不念名字 —— 宁缺一个词，也不把内部标识端给用户。"""
    assert provider_name_for_message(machine) == ""
    text = format_unknown_provider(machine)
    assert text.startswith("Unknown provider.")
    for leaked in MACHINE_IDS:
        assert leaked not in text.lower()
    assert "plobi doctor" in text


def test_retired_machine_ids_are_echoed_like_any_unknown_provider():
    """闭嘴名单跟常量一起删了：这两代现在跟 ``nope-provider`` 一个待遇。

    代价是存量配置里若还写着旧名，报错会把它原样念出来——这是「彻底剔除、
    不做兼容」的必然结果；换来的是代码里不再留任何半条兼容路径。
    """
    for retired in _RETIRED:
        assert provider_name_for_message(retired) == retired
        assert format_unknown_provider(retired) == (
            f"Unknown provider '{retired}'.{FULL_HINT}"
        )


def test_real_provider_name_is_echoed_with_the_full_hint():
    assert format_unknown_provider("qoder") == f"Unknown provider 'qoder'.{FULL_HINT}"
    assert format_unknown_provider("nope-provider") == (
        f"Unknown provider 'nope-provider'.{FULL_HINT}"
    )


def test_short_label_falls_back_to_a_placeholder(config_without_usable_default):
    """``plobi auth`` 那种短文案走 label 版，兜底给占位符而不是内部标识。"""
    assert unknown_provider_label("bezel") == "<internal id>"
    assert unknown_provider_label("qoder") == "qoder"


def test_custom_tail_is_respected():
    text = format_unknown_provider("qoder", tail=" See config.yaml.")
    assert text == "Unknown provider 'qoder'. See config.yaml."


# ---------------------------------------------------------------------------
# 源码守卫：退役的机器标识不许回到代码里（11-19 彻底剔除，零兼容）
# ---------------------------------------------------------------------------

_ROOT = pathlib.Path(__file__).resolve().parents[2]

_RETIRED = ("aigw", "quota_hub")


def test_retired_machine_ids_are_gone_from_the_code():
    """这两代名字在生产代码里零出现，连注释都不留。

    它们既不是 provider、也不是传输名，更没有「闭嘴名单」这种半条兼容路径。
    11-19 主人拍板：「软件里面没有了，这两个词就不会出现了呀」——所以代码里
    任何一处的 ``aigw``／``quota_hub`` 判定（白名单、迁移表、环境变量名单、
    正则、硬写死的文案）都是残留，会重新长出兼容壳。
    """
    offenders: list[str] = []
    for sub in ("plobi_cli", "tui_gateway", "plobi"):
        base = _ROOT / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(_ROOT))
            for lineno, line in enumerate(
                io.open(path, encoding="utf-8", errors="replace").read().splitlines(), 1
            ):
                low = line.lower()
                if not any(name in low for name in _RETIRED):
                    continue
                offenders.append(f"{rel}:{lineno}| {line.strip()[:120]}")

    assert not offenders, (
        "退役的机器标识又出现在代码里了（彻底剔除，一个字母都不留）：\n"
        + "\n".join(offenders)
    )

_SAFE_CALLS = (
    "unknown_provider_label",
    "format_unknown_provider",
    "provider_name_for_message",
)


def test_no_unknown_provider_message_interpolates_a_raw_name():
    """``f"Unknown provider '{provider_id}'"`` 这种写法一律不允许。

    它正是 ``Unknown provider 'aigw'`` 上屏的原因：变量里的机器标识没有经过
    ``provider_name_for_message``。新增文案请走 ``format_unknown_provider`` /
    ``unknown_provider_label``，这两个函数负责把机器标识换成软件名。
    """
    offenders: list[str] = []
    for sub in ("plobi_cli", "tui_gateway", "plobi"):
        base = _ROOT / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in str(path) or path.name == "models.py":
                continue
            for lineno, line in enumerate(
                io.open(path, encoding="utf-8", errors="replace").read().splitlines(), 1
            ):
                low = line.lower()
                if "unknown provider" not in low:
                    continue
                stripped = line.strip()
                if stripped.startswith("#") or "{" not in line:
                    continue
                if any(safe in line for safe in _SAFE_CALLS):
                    continue
                offenders.append(
                    f"{path.relative_to(_ROOT)}:{lineno}| {stripped[:120]}"
                )

    assert not offenders, (
        "这些 'Unknown provider' 文案直接插了裸变量，机器标识会上屏；"
        "改用 format_unknown_provider / unknown_provider_label：\n"
        + "\n".join(offenders)
    )

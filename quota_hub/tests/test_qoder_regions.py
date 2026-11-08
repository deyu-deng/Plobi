"""CN and international Qoder are two account systems, so they are two cards.

User 2026-11-08: 「CN 版和国际版也没有做区分，明明是不同的账号体系」 — 已拍两张卡。Measured
that day on this machine: `api.qoder.com` answers **401** (not connection-failure) on the
device poll and on `/algo/api/v2/model/list`, and the installed international app names that
host; `qoder.com/device/selectAccounts` answers 302 like its CN twin. So the protocol is
shared and only the hosts and the credential file differ.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from quota_hub.providers.apps.qoder import oauth
from quota_hub.providers.apps.qoder.api import QoderProvider


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / "home"))
    return tmp_path


def test_the_two_regions_have_their_own_hosts_and_store_files():
    cn = oauth.hosts("cn")
    intl = oauth.hosts("intl")

    assert cn["base"] == "https://qoder.com.cn"
    assert cn["openapi"] == "https://openapi.qoder.com.cn"
    assert cn["gateway"] == "https://gateway.qoder.com.cn"
    assert intl["base"] == "https://qoder.com"
    assert intl["openapi"] == "https://api.qoder.com"
    assert intl["gateway"] == "https://api.qoder.com"
    # never share a token
    assert oauth.store_path("cn").name != oauth.store_path("intl").name


def test_a_credential_written_for_one_region_is_invisible_to_the_other():
    oauth.write_credential({"token": "cn-token", "user_id": "u1"}, "cn")

    assert oauth.read_credential("cn")["token"] == "cn-token"
    assert oauth.read_credential("intl") is None
    assert oauth.signed_in("intl")[0] is False


def test_the_consent_url_follows_the_region():
    cn = oauth.login_url("challenge", "nonce", "cn")
    intl = oauth.login_url("challenge", "nonce", "intl")

    assert cn.startswith("https://qoder.com.cn/device/selectAccounts?")
    assert intl.startswith("https://qoder.com/device/selectAccounts?")


def test_the_intl_card_speaks_its_own_namespace_and_gateway():
    prov = QoderProvider({"region": "intl", "leg": "qoder_intl",
                                "display_name": "Qoder (International)"}, httpx.AsyncClient(trust_env=False))

    assert prov.base == "https://api.qoder.com", prov.base
    # the published id must start with the card id (that is what the desktop joins on)
    assert prov.model_prefix == "qoder_intl", prov.model_prefix


def test_the_cn_card_is_unchanged_when_no_region_is_named():
    prov = QoderProvider({"display_name": "Qoder"}, httpx.AsyncClient(trust_env=False))

    assert prov.base == "https://gateway.qoder.com.cn", prov.base
    assert prov.model_prefix == "qoder", prov.model_prefix

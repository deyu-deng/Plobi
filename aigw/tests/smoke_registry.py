"""Smoke test: aigw loads the Antigravity config and registers its models."""
import asyncio
import os

os.environ.setdefault("AIGW_CONFIG", "config.antigravity.yaml")

# Avoid the headless Keychain prompt during discovery.
from aigw.tokens import desktop_stores as ds

ds.read_antigravity_access_token = lambda: None
ds.read_antigravity = lambda: None

import httpx
from aigw.config import load
from aigw.registry import Registry


async def main() -> None:
    cfg = load()
    http = httpx.AsyncClient(timeout=10)
    reg = await Registry(cfg, http).build()
    ids = [m["id"] for m in reg.list_models()]
    ag = [i for i in ids if i.startswith("antigravity/")]
    assert ag, f"no antigravity models registered: {ids}"
    print("all models:", ids)
    print("antigravity models registered:", ag)
    await http.aclose()


asyncio.run(main())

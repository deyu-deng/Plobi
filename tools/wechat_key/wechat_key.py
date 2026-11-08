#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信取钥的命令行薄壳——**实现只有一份**，在产品那边
（``plobi/collectors/chatlog/keyscan.py``）。

这一档是给「自己动手」的人用的：产品里点「检查一下」走的是同一条链，
两边不许各写一份实现（同一件事两处真源 = 迟早漂）。

用法::

    # 取一次密钥，写进引擎那一档配置（$PLOBI_HOME/chatlog/chatlog.json）
    python tools/wechat_key/wechat_key.py

    # 「我手上已经有那把 key」——不必再取一次
    python tools/wechat_key/wechat_key.py --set-key <64 个十六进制字符>

    # 写到别处（调试用）
    python tools/wechat_key/wechat_key.py --out D:\\Temp\\chatlog.json

**密钥值一个字都不打**：只报长度与 sha256 前 12 位（不可逆推）。
只读扫描：不写进程内存、不注入 DLL、不 attach 调试器、不杀进程。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from plobi.collectors.chatlog import keyscan  # noqa: E402
from plobi.collectors.chatlog.sidecar import engine_config_dir  # noqa: E402


def _fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description="WeChat key extractor (read-only)")
    parser.add_argument("--out", default="",
                        help="写入路径（默认引擎那一档配置 $PLOBI_HOME/chatlog/chatlog.json）")
    parser.add_argument("--db", default="",
                        help="指定用来校验的聊天库（默认自己从微信进程认）")
    parser.add_argument("--set-key", default="",
                        help="把手上已有的那把 key（64 个十六进制字符）写进配置，不再取")
    args = parser.parse_args()

    if args.set_key:
        target = Path(args.out) if args.out else engine_config_dir(os.environ) / "chatlog.json"
        try:
            keyscan.set_key(target.parent, args.set_key)
        except ValueError as exc:
            print("[fail] %s" % exc)
            return 2
        print("[ok] 已写入 %s（值不打屏）" % target)
        return 0

    key, note, extra = keyscan.extract(args.db)
    if key is None:
        print("[fail] %s" % note)
        return 1
    print("[ok] 取到密钥：len=%d sha256[:12]=%s（值不打屏）" % (len(key), _fingerprint(key)))
    print("[info] 读数：data_dir=%s version=%s platform=%s"
          % (extra.get("data_dir"), extra.get("version"), extra.get("platform")))
    target = Path(args.out) if args.out else engine_config_dir(os.environ) / "chatlog.json"
    keyscan.write_handoff(target.parent, key, extra)
    print("[info] 已写入 %s" % target)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""``plobi say`` —— 把 say 那一枚嘴交给**不在 Plobi 工具循环里**的说话者。

为什么需要这一档（用户 2026-10-22）：外包出去的 L2 跑在外部软件自己的进程里，它拿不到
``say`` 这枚模型工具，于是它「干完了却开不了口」——只能等 ``external_offload_run`` 把
返回值带回来。这一档补的是**把手**，不是第二张嘴：投递仍然只走
:func:`channel.handle_say` 那一条路，落点、留痕、唤醒与内部 Agent 用的 ``say`` 逐字相同
（裁定 94：一条事实一处真源；桌面侧栏可见性靠的就是那个共用落点，见 channel.py 里
``_deliver`` 的注记）。

**故意没有 ``--as``。** 说话者是谁只认 ``PLOBI_AGENT_ID``（:func:`channel.current_agent_name`
那一只现成的读路，由派发方在子进程环境里带好）。命令行上能填身份 = 谁都能冒充任何一个
Agent 对别人说话，那不是把手，是伪造入口。
"""

from __future__ import annotations

import json
from typing import Any

from . import channel as CH


def setup(parser: Any) -> None:
    """argparse 那一格。``--text`` / ``--path`` 可重复，顺序就是段序。"""
    parser.add_argument("--to", required=True,
                        help="接收方的 Agent 名字（名册里那一行，显示名也认）")
    parser.add_argument("--text", action="append", default=[],
                        help="一段话。要给多段就重复这个参数")
    parser.add_argument("--path", action="append", default=[],
                        help="一个文件指针（相对 vault/·mind/·共享文件根）。只给路径，"
                             "内容不贴进来——接话方自己读（裁 183③）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="把回执按 JSON 打到 stdout（给脚本读；默认打一句人话）")


def run(args: Any) -> int:
    """组 parts → 调那一条共用的投递 → 按 ``ok`` 决定退出码。"""
    parts = [{"text": text} for text in (args.text or [])]
    parts += [{"path": path} for path in (args.path or [])]

    raw = CH.handle_say({"to": args.to, "parts": parts})
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = {"ok": False, "error": str(raw)[:400]}

    if getattr(args, "as_json", False):
        print(json.dumps(payload, ensure_ascii=False))
    elif payload.get("ok"):
        woke = "已经把它叫醒" if payload.get("woke") else f"但没叫醒它：{payload.get('wakeNote') or '原因没报'}"
        print(f"送到了：{payload.get('to')}（消息 {payload.get('message_id')}，{woke}）")
    else:
        print(f"没送出去：{payload.get('error') or '未知原因'}")
        reachable = payload.get("reachable")
        if reachable:
            print("名册里现在可发的：" + "、".join(reachable))

    return 0 if payload.get("ok") else 1

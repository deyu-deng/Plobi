"""Office 文档门类的四只手 —— 薄包装本机的 OfficeCLI。

为什么是四枚工具而不是一枚带 `action` 的：裁定 185，一枚工具只干一件事。
OfficeCLI 本身是 CLI（`officecli set/get/validate/view`），这里只把「找到二进制
+ 拼命令 + 收输出」这三件事收进产品，Agent 不用记参数、也不用自己找路径。

二进制落点（按顺序试，第一个能跑的就用）：
  1. PATH 里的 ``officecli``
  2. ``$PLOBI_HOME/bin/officecli[.exe]``（产品自带）
都没有就明确报错——**不许假装能改**，那是 10-27 那版 skill 的毛病（写死
``D:\\Portables\\cli\\officecli\\officecli.exe``，换台机器就废）。

改文件之前留快照是 skill 的纪律，这里不代做：工具只负责把这一步做对，
备份位置由调用方决定（同目录 ``.<name>.plobi-backup-<timestamp>``）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from tools.registry import registry, tool_error

_TIMEOUT_SECONDS = 120

_READ_MODES = ("outline", "stats", "issues", "text")

_STR = {"type": "string"}


def find_officecli() -> Optional[str]:
    """PATH → $PLOBI_HOME/bin。返回可执行文件路径，找不到返回 None。"""
    found = shutil.which("officecli")

    if found:
        return found

    home = os.environ.get("PLOBI_HOME") or ""

    if not home:
        return None

    for name in ("officecli.exe", "officecli"):
        candidate = Path(home) / "bin" / name

        if candidate.is_file():
            return str(candidate)

    return None


def _run(*argv: str) -> tuple[int, str, str]:
    exe = find_officecli()

    if not exe:
        raise FileNotFoundError(
            "本机没有 officecli（PATH 与 $PLOBI_HOME/bin/ 都没找到）。"
            "装好之后才能改 Office 文件；不要用别的库硬凑。"
        )

    proc = subprocess.run(
        [exe, *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT_SECONDS,
    )

    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _fail(stage: str, rc: int, out: str, err: str) -> str:
    detail = (err or out or "").strip() or f"officecli 退出码 {rc}"

    return tool_error(f"{stage}失败：{detail}", success=False)


def office_read(args: dict, **_kwargs) -> str:
    """读结构 / 统计 / 校验问题 / 全文。"""
    path = str(args.get("file") or "").strip()
    mode = str(args.get("mode") or "outline").strip().lower()

    if not path:
        return tool_error("file 必填：要读哪个 Office 文件")
    if mode not in _READ_MODES:
        return tool_error(f"mode 只认 {', '.join(_READ_MODES)}，收到 {mode!r}")

    try:
        rc, out, err = _run("view", path, mode)
    except FileNotFoundError as exc:
        return tool_error(str(exc), success=False)
    except subprocess.TimeoutExpired:
        return tool_error("officecli 读超时（120s）", success=False)

    if rc != 0:
        return _fail("读", rc, out, err)

    return json.dumps({"ok": True, "mode": mode, "text": out}, ensure_ascii=False)


def office_edit(args: dict, **_kwargs) -> str:
    """改一个节点（格 / 段落 / 形状）的属性，并落盘。"""
    path = str(args.get("file") or "").strip()
    target = str(args.get("path") or "").strip()
    props = args.get("props") or {}

    if not path:
        return tool_error("file 必填")
    if not target:
        return tool_error("path 必填，例如 /Sheet1/A1 或 /body/p[1]")
    if not isinstance(props, dict) or not props:
        return tool_error('props 必填，例如 {"value": "42"}')

    argv = ["set", path, target]

    for key, value in props.items():
        argv += ["--prop", f"{key}={value}"]

    try:
        rc, out, err = _run(*argv)
    except FileNotFoundError as exc:
        return tool_error(str(exc), success=False)
    except subprocess.TimeoutExpired:
        return tool_error("officecli 改超时（120s）", success=False)

    if rc != 0:
        return _fail("改", rc, out, err)

    try:
        save_rc, save_out, save_err = _run("save", path)
    except subprocess.TimeoutExpired:
        return tool_error("改动已应用，但保存超时——文件可能还没落盘，重跑一次 save", success=False)

    if save_rc != 0:
        return _fail("保存", save_rc, save_out, save_err)

    return json.dumps(
        {"ok": True, "applied": out.strip(), "saved": save_out.strip(), "file": path, "path": target},
        ensure_ascii=False,
    )


def office_validate(args: dict, **_kwargs) -> str:
    """按 OpenXML schema 校验。"""
    path = str(args.get("file") or "").strip()

    if not path:
        return tool_error("file 必填")

    try:
        rc, out, err = _run("validate", path)
    except FileNotFoundError as exc:
        return tool_error(str(exc), success=False)
    except subprocess.TimeoutExpired:
        return tool_error("officecli 校验超时（120s）", success=False)

    clean = rc == 0 and "no errors" in (out or "").lower()

    return json.dumps(
        {"ok": clean, "clean": clean, "text": (out or err).strip()}, ensure_ascii=False
    )


def office_render(args: dict, **_kwargs) -> str:
    """渲一页成图，供人眼看版式。"""
    path = str(args.get("file") or "").strip()
    page = args.get("page", 1)

    if not path:
        return tool_error("file 必填")

    try:
        page_no = int(page)
    except (TypeError, ValueError):
        return tool_error(f"page 要整数，收到 {page!r}")

    try:
        rc, out, err = _run("view", path, "screenshot", "--page", str(page_no))
    except FileNotFoundError as exc:
        return tool_error(str(exc), success=False)
    except subprocess.TimeoutExpired:
        return tool_error("officecli 渲染超时（120s）", success=False)

    if rc != 0:
        return _fail("渲染", rc, out, err)

    return json.dumps({"ok": True, "page": page_no, "text": out.strip()}, ensure_ascii=False)


_READ_SCHEMA = {
    "name": "office_read",
    "description": (
        "读一个 Office 文件（DOCX / XLSX / PPTX）的结构。"
        "mode=outline 看骨架、stats 看规模、issues 看有哪些问题、text 看全文。"
        "改之前先读——不知道路径长什么样就别猜。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file": {**_STR, "description": "文件的绝对路径"},
            "mode": {"type": "string", "enum": list(_READ_MODES), "description": "读哪一种视图，默认 outline"},
        },
        "required": ["file"],
    },
}

_EDIT_SCHEMA = {
    "name": "office_edit",
    "description": (
        "改 Office 文件里的一个节点，然后落盘。"
        "path 形如 /Sheet1/A1（表格）、/body/p[1]（Word）、/slide[1]/shape[2]（PPT），索引从 1 开始。"
        "props 是 属性→值 的映射，例如 {\"value\": \"42\"}。"
        "改多个地方用连续调用；每次都会保存。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file": {**_STR, "description": "文件的绝对路径"},
            "path": {**_STR, "description": "节点路径，例如 /Sheet1/A1"},
            "props": {"type": "object", "description": "要改的属性，例如 {\"value\": \"42\"}"},
        },
        "required": ["file", "path", "props"],
    },
}

_VALIDATE_SCHEMA = {
    "name": "office_validate",
    "description": "按 OpenXML schema 校验一个 Office 文件，返回干净与否。改完必跑。",
    "parameters": {
        "type": "object",
        "properties": {"file": {**_STR, "description": "文件的绝对路径"}},
        "required": ["file"],
    },
}

_RENDER_SCHEMA = {
    "name": "office_render",
    "description": "把一个 Office 文件渲成一页图，用来自己看版式对不对。",
    "parameters": {
        "type": "object",
        "properties": {
            "file": {**_STR, "description": "文件的绝对路径"},
            "page": {"type": "integer", "description": "第几页，从 1 开始，默认 1"},
        },
        "required": ["file"],
    },
}

registry.register(
    name="office_read",
    toolset="office",
    schema=_READ_SCHEMA,
    handler=office_read,
    description=_READ_SCHEMA["description"],
    emoji="📄",
)

registry.register(
    name="office_edit",
    toolset="office",
    schema=_EDIT_SCHEMA,
    handler=office_edit,
    description=_EDIT_SCHEMA["description"],
    emoji="📄",
)

registry.register(
    name="office_validate",
    toolset="office",
    schema=_VALIDATE_SCHEMA,
    handler=office_validate,
    description=_VALIDATE_SCHEMA["description"],
    emoji="📄",
)

registry.register(
    name="office_render",
    toolset="office",
    schema=_RENDER_SCHEMA,
    handler=office_render,
    description=_RENDER_SCHEMA["description"],
    emoji="📄",
)

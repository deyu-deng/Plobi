"""改名写回：把 Plobi 侧给会话起的名字，真落到**对方软件自己那一格**。

**写不写由真源表说了算**：`agent/vendor_registry.py::SessionSpec.title_write`。
读法、写回登记在同一行，这里不另立名单——多一份名单就多一处漂移。

三条路（对应 `title_write` 三个取值）：

* ``jsonl-record``：改 jsonl 里**厂商自起名那条记录**的标题格
  （CodeBuddy 的 ``type=ai-title`` 记录／``aiTitle``）。它自己没起过名 ⇒ 无处可写，
  如实回 ``no_slot``，不硬塞一条假记录。
* ``sidecar-json``：改会话旁挂元数据的标题格（Kiro 的 ``<会话号>.json``／``title``）。
* ``sqlite-column``：``UPDATE <主表> SET <标题列>``
  （Marvis／agy／OpenCode／Kilo／Copilot）。

**为什么不是每家都写**：

* B 档（Gemini／Cline／Cursor／Qoder 那一族）标题就是**第一句人话**，写回等于改聊天内容；
* C 档（Qoder 桌面 App）会话索引在它自己进程里，本地写了它不认（10-28 实测）。
⇒ 这两档一律不写，回 ``ledger_only``，界面上要标清「只在 Plobi 里生效」。

三条铁律（11-22 用户拍板「尽量都适配」时定的）：

1. **软件开着不写**——它内存里的旧名字／缓存回写会把我们的改动吃掉；
2. **写前备份**——原文件（或整库）先拷进 ``<PlobiHome>/external_sessions/title_backups/``；
3. **写完读回校验**——读不回新名字就报失败，绝不静默成功。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

#: 名字最长留这么多字（对方软件那一格普遍不大，超了会被它自己截断）。
MAX_TITLE_CHARS = 120

#: 各家的进程名（小写、无 ``.exe``）——用来判「软件开着没」。
#:
#: **桌面 App（Marvis）故意不登记**：用户此刻就在用它，探到「在跑」就永远写不进去，
#: 那这功能等于废掉；SQLite 本身扛并发写，风险交给「写前备份 + 读回校验」兜。
PROCESS_HINTS: dict[str, tuple[str, ...]] = {
    "codebuddy": ("codebuddy",),
    "kiro": ("kiro-cli", "kiro"),
    "agy": ("agy", "antigravity"),
    "opencode": ("opencode",),
    "kilo": ("kilo",),
    "copilot": ("copilot",),
}

#: 写回总闸——排查时用环境变量一把关掉（``PLOBI_TITLE_WRITEBACK=0``）。
DISABLE_ENV = "PLOBI_TITLE_WRITEBACK"

_SQL_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _es():
    """读法那一套（路径解析、spec 表）都在 `external_sessions`，这里只借不抄。"""
    from plobi.delegation import external_sessions

    return external_sessions


def _spec(vendor: str):
    from agent.vendor_registry import SESSION_SPECS

    return SESSION_SPECS.get(str(vendor or "").strip().lower())


def _label(vendor: str) -> str:
    """这家软件给人看的名字。

    真源表里没登记显示名（``VendorRow`` 只登记事实，不登记包装），就照原样用它自己的 id
    —— ``codebuddy``／``kiro`` 这些本来也认得出，别在这里另编一套译名。
    """
    return str(vendor or "").strip()


def _mode(vendor: str) -> str:
    return str(getattr(_spec(vendor), "title_write", "") or "")


def writable(vendor: str) -> bool:
    """这家能不能真写回——只在 Plobi 侧生效的那些一律 False。"""
    return bool(_mode(vendor))


def describe(vendor: str) -> str:
    """一句话说清「改这个名字会不会真写回它自己」（给界面/工具描述用）。"""
    mode = _mode(vendor)
    if mode == "sqlite-column":
        return f"{_label(vendor)}：会写回它自己的会话名（改完刷新它就能看见）"
    if mode == "jsonl-record":
        return f"{_label(vendor)}：会写回它自己起的标题（它还没起过名时只改 Plobi 这边）"
    if mode == "sidecar-json":
        return f"{_label(vendor)}：会写回它的会话元数据（它自己就能看见新名字）"
    return f"{_label(vendor)}：标题是它的聊天内容本身，改不了 ⇒ 这个名字只在 Plobi 里生效"


def _clip(title: Any) -> str:
    text = " ".join(str(title or "").split())
    return text[:MAX_TITLE_CHARS]


def _running(vendor: str) -> str:
    """软件开着就返回进程名，没开/探不到返回空串。"""
    hints = PROCESS_HINTS.get(str(vendor or "").strip().lower()) or ()
    if not hints:
        return ""
    try:
        import psutil
    except ImportError:  # 没有 psutil 就不拦，靠备份兜底
        return ""
    for proc in psutil.process_iter(["name"]):
        name = str(proc.info.get("name") or "").strip().lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if not name:
            continue
        for hint in hints:
            if name == hint or name.startswith(hint):
                return str(proc.info.get("name") or hint)
    return ""


def _backup_dir(vendor: str) -> Path:
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "external_sessions" / "title_backups" / str(vendor or "unknown")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup_file(vendor: str, path: Path) -> Optional[Path]:
    try:
        target = _backup_dir(vendor) / f"{path.name}.{_stamp()}.bak"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target
    except OSError:
        return None


def _backup_db(vendor: str, db: Path) -> Optional[Path]:
    """整库先落一份（走 SQLite 自己的 backup，WAL 里的未合并页也带得走）。"""
    try:
        target = _backup_dir(vendor) / f"{db.name}.{_stamp()}.bak"
        target.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            sink = sqlite3.connect(str(target))
            try:
                source.backup(sink)
            finally:
                sink.close()
        finally:
            source.close()
        return target
    except (OSError, sqlite3.Error):
        return _backup_file(vendor, db)


def _write_atomic(path: Path, text: str) -> None:
    """先写旁路临时文件再顶替——半截文件比不改更糟。"""
    tmp = path.with_name(f"{path.name}.plobi-tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _db_path(spec, session_id: str) -> Optional[Path]:
    if not spec.db_resolver and not spec.db_rule:
        return None
    try:
        db = _es()._resolved(spec.db_resolver, spec.db_rule)
    except Exception:  # noqa: BLE001 —— 路径规矩坏了就当找不到库
        return None
    return db if db and Path(db).exists() else None


def _jsonl_path(spec, session_id: str) -> Optional[Path]:
    try:
        root = _es()._resolved(spec.root_resolver, spec.root_rule)
    except Exception:  # noqa: BLE001
        return None
    if not root or not Path(root).exists():
        return None
    pattern = spec.transcript_glob or "*/{session_id}.jsonl"
    hits = sorted(Path(root).glob(pattern.format(session_id=session_id)))
    return hits[0] if hits else None


def _sidecar_path(spec, session_id: str) -> Optional[Path]:
    if not spec.sidecar_glob:
        return None
    try:
        root = _es()._resolved(spec.root_resolver, spec.root_rule)
    except Exception:  # noqa: BLE001
        return None
    if not root:
        return None
    path = Path(root) / spec.sidecar_glob.format(session_id=session_id)
    return path if path.exists() else None


def _read_back(spec, session_id: str) -> str:
    """写完照原路读回来一眼——读不回新名字就算没写成。"""
    mode = str(getattr(spec, "title_write", "") or "")
    if mode == "sqlite-column":
        db = _db_path(spec, session_id)
        if db is None:
            return ""
        table, id_col = str(spec.table or ""), str(spec.id_column or "")
        column = str(spec.title_column or "")
        if not (_SQL_IDENT.match(table) and _SQL_IDENT.match(id_col) and _SQL_IDENT.match(column)):
            return ""
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5.0)
        except sqlite3.Error:
            return ""
        try:
            row = con.execute(
                f'SELECT "{column}" FROM "{table}" WHERE "{id_col}" = ?', (session_id,)
            ).fetchone()
        except sqlite3.Error:
            return ""
        finally:
            con.close()
        return str(row[0] or "").strip() if row else ""
    if mode == "sidecar-json":
        path = _sidecar_path(spec, session_id)
        if path is None:
            return ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        value = payload.get(spec.sidecar_title_field) if isinstance(payload, dict) else None
        return str(value or "").strip()
    if mode == "jsonl-record":
        path = _jsonl_path(spec, session_id)
        if path is None:
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        for raw in lines:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and str(record.get("type") or "") == spec.title_record_type:
                return str(record.get(spec.title_record_field) or "").strip()
    return ""


def _write_sqlite(spec, session_id: str, title: str) -> dict[str, Any]:
    db = _db_path(spec, session_id)
    if db is None:
        return {"ok": False, "status": "not_found", "note": f"没找到 {_label(spec.vendor)} 的会话库"}
    table, id_col = str(spec.table or ""), str(spec.id_column or "")
    column = str(spec.title_column or "")
    if not (_SQL_IDENT.match(table) and _SQL_IDENT.match(id_col) and _SQL_IDENT.match(column)):
        return {"ok": False, "status": "failed", "note": "真源表里这条的库表／列名不像正经名字，不写"}
    backup = _backup_db(spec.vendor, db)
    try:
        con = sqlite3.connect(str(db), timeout=5.0)
    except sqlite3.Error as exc:
        return {"ok": False, "status": "failed", "note": f"打不开它的会话库：{exc}"}
    try:
        con.execute("PRAGMA busy_timeout = 5000")
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            f'UPDATE "{table}" SET "{column}" = ? WHERE "{id_col}" = ?', (title, session_id)
        )
        if not cur.rowcount:
            con.rollback()
            return {
                "ok": False,
                "status": "not_found",
                "note": f"{_label(spec.vendor)} 的库里没有这条会话（它可能已经删了）",
            }
        con.commit()
    except sqlite3.Error as exc:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        return {"ok": False, "status": "failed", "note": f"写它的会话库失败：{exc}", "backup": _p(backup)}
    finally:
        con.close()
    return {"ok": True, "status": "written", "path": str(db), "backup": _p(backup)}


def _write_jsonl(spec, session_id: str, title: str) -> dict[str, Any]:
    path = _jsonl_path(spec, session_id)
    if path is None:
        return {"ok": False, "status": "not_found", "note": f"没找到 {_label(spec.vendor)} 这条会话的正文"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "status": "failed", "note": f"读不了它的会话文件：{exc}"}
    hit = False
    out: list[str] = []
    for raw in lines:
        if not raw.strip():
            out.append(raw)
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            out.append(raw)
            continue
        if isinstance(record, dict) and str(record.get("type") or "") == spec.title_record_type:
            record[spec.title_record_field] = title
            out.append(json.dumps(record, ensure_ascii=False))
            hit = True
        else:
            out.append(raw)
    if not hit:
        return {
            "ok": False,
            "status": "no_slot",
            "note": f"{_label(spec.vendor)} 自己还没给这条会话起过名 ⇒ 它没地方存这个名字，"
            f"这个名字只在 Plobi 里生效",
            "path": str(path),
        }
    backup = _backup_file(spec.vendor, path)
    try:
        _write_atomic(path, "\n".join(out) + "\n")
    except OSError as exc:
        return {"ok": False, "status": "failed", "note": f"写它的会话文件失败：{exc}", "backup": _p(backup)}
    return {"ok": True, "status": "written", "path": str(path), "backup": _p(backup)}


def _write_sidecar(spec, session_id: str, title: str) -> dict[str, Any]:
    path = _sidecar_path(spec, session_id)
    if path is None:
        return {
            "ok": False,
            "status": "no_slot",
            "note": f"{_label(spec.vendor)} 这条会话没有旁挂元数据文件 ⇒ 名字没处存，"
            f"这个名字只在 Plobi 里生效",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "failed", "note": f"读不了它的会话元数据：{exc}"}
    if not isinstance(payload, dict):
        return {"ok": False, "status": "failed", "note": "它的会话元数据不是个字典，不敢动"}
    backup = _backup_file(spec.vendor, path)
    payload[spec.sidecar_title_field] = title
    try:
        _write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        return {"ok": False, "status": "failed", "note": f"写它的会话元数据失败：{exc}", "backup": _p(backup)}
    return {"ok": True, "status": "written", "path": str(path), "backup": _p(backup)}


def _p(path: Optional[Path]) -> str:
    return str(path) if path else ""


def _ledger_only(vendor: str) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ledger_only",
        "mode": "",
        "verified": False,
        "note": describe(vendor) + "。界面上按「只在 Plobi 里生效」标清，别让人以为对方那边也变了",
    }


def _disabled() -> bool:
    return str(os.environ.get(DISABLE_ENV, "")).strip() in {"0", "false", "off", "no"}


def writeback_title(vendor: str, session_id: str, title: Any, *, force: bool = False) -> dict[str, Any]:
    """把 ``title`` 写回 ``vendor`` 那条会话；返回真话（写没写成、写到哪、备份在哪）。

    ``status`` 取值：``written``（对方那边真变了）／``ledger_only``（这家本来就写不了）／
    ``busy``（软件开着，先关掉）／``no_slot``（它自己没起过名，没格子可写）／
    ``not_found``（会话没了）／``failed``（真写失败，看 note）。
    """
    new_title = _clip(title)
    if not new_title:
        return {"ok": False, "status": "failed", "note": "名字是空的，不写"}
    spec = _spec(vendor)
    if spec is None:
        return {"ok": False, "status": "failed", "note": f"不认识这家软件：{vendor}"}
    mode = _mode(vendor)
    if not mode:
        return _ledger_only(spec.vendor)
    if _disabled():
        return {
            "ok": True,
            "status": "ledger_only",
            "mode": mode,
            "note": f"{DISABLE_ENV}=0 已关掉写回 ⇒ 这次只改了 Plobi 这边",
        }
    if not force:
        running = _running(spec.vendor)
        if running:
            return {
                "ok": False,
                "status": "busy",
                "mode": mode,
                "note": f"{_label(spec.vendor)} 正开着（{running}）。先把它关掉再改名，"
                f"不然它会把旧名字写回去 —— 这次没动它",
            }
    if mode == "sqlite-column":
        result = _write_sqlite(spec, str(session_id), new_title)
    elif mode == "jsonl-record":
        result = _write_jsonl(spec, str(session_id), new_title)
    elif mode == "sidecar-json":
        result = _write_sidecar(spec, str(session_id), new_title)
    else:
        return {"ok": False, "status": "failed", "note": f"写回方式没实现：{mode}"}
    result["mode"] = mode
    result["title"] = new_title
    if not result.get("ok"):
        return result
    back = _read_back(spec, str(session_id))
    result["verified"] = bool(back) and back.splitlines()[0].strip() == new_title
    if not result["verified"]:
        result["ok"] = False
        result["status"] = "failed"
        result["note"] = (
            f"写进去了但读回来对不上（读回的是「{back[:40] or '空'}」）⇒ 当作没写成；"
            f"原文件备份在 {result.get('backup') or '（备份也没成，注意）'}"
        )
        return result
    result.setdefault("note", "")
    return result


def status_line(result: dict[str, Any], vendor: str) -> str:
    """把写回结果翻成一句人话（工具回话、界面提示都用这一句）。"""
    status = str(result.get("status") or "")
    if status == "written":
        return f"已写回 {_label(vendor)} 自己那边（读回校验通过）"
    if status == "ledger_only":
        return str(result.get("note") or describe(vendor))
    if status == "busy":
        return str(result.get("note") or "对方软件开着，先关掉再改名")
    if status == "no_slot":
        return str(result.get("note") or "它自己没给这条会话起过名，没地方存新名字")
    return str(result.get("note") or "写回没成")


def summary() -> dict[str, Any]:
    """写回能力总览（自检脚本／界面「支持哪些」用）。"""
    from agent.vendor_registry import SESSION_SPECS

    writable_vendors = {
        vendor: spec.title_write
        for vendor, spec in SESSION_SPECS.items()
        if str(getattr(spec, "title_write", "") or "")
    }
    return {
        "writable": writable_vendors,
        "readable_not_writable": [
            vendor for vendor in SESSION_SPECS if vendor not in writable_vendors
        ],
        "disabled": _disabled(),
        "python": sys.version.split()[0],
    }

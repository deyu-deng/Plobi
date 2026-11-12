#!/usr/bin/env python3
"""安装位哨兵：证明「只改源码，不动安装位」（2026-11-12 立）。

为什么要有它：全程纪律 §3.7（`Docs/runbooks/dev-workflow.md` 附录 A）规定，修 bug 只在源码仓
改、跑测试、提交，`D:\\Software\\Plobi`（含 `resources\\backend`、`install-stamp.json`）与其数据家
对 Agent 只读。这条纪律**改安装位根本不进 git**——装完之后手补一个模块、覆盖一个 exe、往 bundled
backend 里塞个 py，任何代码门禁都看不见，只有下一版装机时才以「线上复现不了 / 开发态好好的」的
面目出现。所以这里给安装位留一份指纹底档：发版或重装后 `record` 一次，Agent 收尾 `check` 一次，
被手改过就红。

用法：
    python scripts/plobi/check_install_guard.py record          # 记基线（装完之后、确认干净的那一刻）
    python scripts/plobi/check_install_guard.py check           # 自核：动过即 exit 1
    python scripts/plobi/check_install_guard.py show            # 打印当前基线与现场

    --install-root PATH   安装位根（默认识别顺序：本参数 → $PLOBI_INSTALL_ROOT → 注册表卸载项 DisplayIcon）
    --data-home PATH      数据家（默认识别顺序：本参数 → $PLOBI_HOME → plobi_constants.get_plobi_home()）
    --state-dir PATH      基线落点（默认 <data-home>\\guard）
    --deep                对 exe / app.asar 做 sha256（慢几秒；判「体积和 mtime 都没变但内容变了」时才需要）
    --json                机器可读
    -v                    check 时把差异全列出来（默认每个类别只列前 30 条）

判据（exit code）：
    0  clean，或「安装包本身换了」（stamp 变了 = 发版/重装，提示复核后 record，不算违例）
    1  违例：stamp 没变，但安装位里有文件被增 / 删 / 改
    2  没法判：定位不到安装位、或还没有基线

基线跟着**这台机器**走，不进仓（各机安装位不同），默认落 `<data-home>\\guard\\install-fingerprint.json.gz`。
§3.7 里「安装位与其数据家对 Agent 只读」的唯一例外就是这个 `guard/` 目录——它是哨兵自己的账本。
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

SCHEMA = 1
BASELINE_NAME = "install-fingerprint.json.gz"

#: 运行时自己就会写的东西。它们变了不代表有人 patch 了安装位，混进来只会天天报假红。
#: 判据是「跑一次应用就可能变」——缓存、日志、Chromium 的 profile 侧产物、Python 字节码。
EXCLUDED_DIRS = {
    "logs", "Logs", "Crashpad", "GPUCache", "GPUCache2", "DawnGraphiteCache",
    "DawnWebGPUCache", "Code Cache", "Cache", "CachedData", "blob_storage",
    "Local Storage", "Session Storage", "Network", "Service Worker",
    "Shared Dictionary", "Dictionaries", "__pycache__", ".pytest_cache",
}
EXCLUDED_SUFFIXES = (".log", ".log.old", ".tmp", ".pyc", ".pyo", ".ldb", ".lock")

#: 体积和 mtime 都可能保持不变的少量关键文件；`--deep` 时补 sha256。
DEEP_FILES = ("Plobi.exe", os.path.join("resources", "app.asar"),
              "Uninstall Plobi.exe", "resources.pak")


# --------------------------------------------------------------------------- 环境探测

def _reg_install_roots() -> list[str]:
    """从卸载项捞安装位。`InstallLocation` 这台是空的，可靠的是 `DisplayIcon`（`...\\Plobi.exe,0`）。"""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except Exception:
        return []
    found: list[str] = []
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in roots:
        try:
            with winreg.OpenKey(hive, base) as k:
                count = winreg.QueryInfoKey(k)[0]
                subs = [winreg.EnumKey(k, i) for i in range(count)]
        except OSError:
            continue
        for sub in subs:
            try:
                with winreg.OpenKey(hive, base + "\\" + sub) as k:
                    try:
                        name = str(winreg.QueryValueEx(k, "DisplayName")[0])
                    except OSError:
                        continue
                    if "plobi" not in name.lower():
                        continue
                    for value in ("DisplayIcon", "UninstallString"):
                        try:
                            raw = str(winreg.QueryValueEx(k, value)[0])
                        except OSError:
                            continue
                        exe = raw.split(",")[0].strip().strip('"')
                        if exe.lower().endswith(".exe"):
                            found.append(os.path.dirname(exe))
            except OSError:
                continue
    out: list[str] = []
    for path in found:
        real = os.path.normcase(os.path.abspath(path))
        if real not in {os.path.normcase(os.path.abspath(p)) for p in out}:
            out.append(os.path.abspath(path))
    return out


def resolve_install_root(explicit: str | None) -> tuple[str | None, str]:
    if explicit:
        return (os.path.abspath(explicit), "--install-root") if os.path.isdir(explicit) else (None, "--install-root 不是目录")
    env = os.environ.get("PLOBI_INSTALL_ROOT", "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env), "$PLOBI_INSTALL_ROOT"
    for cand in _reg_install_roots():
        if os.path.isdir(cand):
            return cand, "注册表卸载项 DisplayIcon"
    return None, "定位不到（没有 $PLOBI_INSTALL_ROOT，注册表卸载项也没登记 DisplayIcon）"


def resolve_data_home(explicit: str | None) -> tuple[str | None, str]:
    if explicit:
        return (os.path.abspath(explicit), "--data-home") if os.path.isdir(explicit) else (None, "--data-home 不是目录")
    env = os.environ.get("PLOBI_HOME", "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env), "$PLOBI_HOME"
    try:
        from plobi_constants import get_plobi_home  # type: ignore

        home = get_plobi_home()
        return (str(home), "plobi_constants.get_plobi_home()") if os.path.isdir(str(home)) else (None, "get_plobi_home() 指向的目录不存在")
    except Exception as exc:  # noqa: BLE001 - 仓外跑时导入失败是可预期的
        return None, f"没设 $PLOBI_HOME，也没有可导入的 plobi_constants（{exc}）"


def _reg_data_dirs() -> list[str]:
    """这台机器上「家」的所有可能落点，按可信度排。

    这台机器实测就有两个家：``HKCU\\Environment\\PLOBI_HOME`` 钉着开发家
    （``...\\Plobi-Dev``），而安装器记的 ``HKCU\\Software\\Plobi\\DataDir`` 是装机版的家
    （``...\\Plobi``）。谁先谁后决定基线落在哪个家，所以两边都列出来，`check` 时也能
    互相回退找账本 —— 否则换个 shell（有/没有被 `setx` 钉过的 env）就找不到基线，
    报一句 rc=2 却没人知道其实是找错了家。
    """
    out: list[str] = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, sub, name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Plobi", "DataDir"),
                (winreg.HKEY_CURRENT_USER, "Environment", "PLOBI_HOME"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        value = str(winreg.QueryValueEx(key, name)[0] or "").strip()
                except OSError:
                    continue
                if value and os.path.isdir(value):
                    out.append(os.path.abspath(value))
        except Exception:  # noqa: BLE001
            pass
    for cand in (os.environ.get("PLOBI_HOME", "").strip(),):
        if cand and os.path.isdir(cand):
            out.append(os.path.abspath(cand))
    try:
        from plobi_constants import get_plobi_home  # type: ignore

        home = str(get_plobi_home())
        if os.path.isdir(home):
            out.append(os.path.abspath(home))
    except Exception:  # noqa: BLE001
        pass
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        fallback = os.path.join(local, "plobi")
        if os.path.isdir(fallback):
            out.append(os.path.abspath(fallback))
    uniq: list[str] = []
    for path in out:
        if os.path.normcase(path) not in {os.path.normcase(p) for p in uniq}:
            uniq.append(path)
    return uniq


def resolve_state_dir(explicit: str | None, for_check: bool) -> tuple[str | None, str]:
    """基线落点（`<家>\\guard`）。

    `record` 取**安装器记的那个家**优先（账本跟着装机版走，不往开发家里塞文件）；
    `check` 则谁有账本用谁，并列出看过的所有候选，省得"找不到基线"变成猜谜。
    """
    if explicit:
        return os.path.abspath(explicit), "--state-dir"
    candidates = _reg_data_dirs()
    tried: list[str] = []
    for home in candidates:
        state = os.path.join(home, "guard")
        tried.append(state)
        if not for_check or os.path.isfile(baseline_path(state)):
            return state, f"{home} 下的 guard"
    if for_check:
        return None, "看过这些地方都没有基线：" + ("；".join(tried) or "(没有可用的家)")
    return os.path.join(REPO_ROOT, ".guard"), "仓内 .guard（这台机器上没找到任何家，只是兜底）"


# --------------------------------------------------------------------------- 采集

def _sha256(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _skip_dir(name: str) -> bool:
    return name in EXCLUDED_DIRS


def scan_tree(root: str, deep: bool = False) -> dict:
    """安装位的文件级指纹：相对路径 → "size:mtime_ns"。"""
    files: dict[str, str] = {}
    deep_digests: dict[str, str] = {}
    total = 0
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        for name in sorted(filenames):
            if name.lower().endswith(EXCLUDED_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files[rel] = f"{st.st_size}:{st.st_mtime_ns}"
            total += st.st_size
            newest = max(newest, st.st_mtime)
            if deep and rel in {p.replace(os.sep, "/") for p in DEEP_FILES}:
                try:
                    deep_digests[rel] = _sha256(full)
                except OSError:
                    pass
    return {
        "files": files,
        "deep": deep_digests,
        "file_count": len(files),
        "total_bytes": total,
        "newest_mtime": datetime.datetime.fromtimestamp(newest).isoformat(timespec="seconds") if newest else None,
    }


def read_install_stamp(root: str) -> dict | None:
    """`resources/install-stamp.json` —— 这一版是哪个 commit 编出来的。变了就等于换了安装包。"""
    path = os.path.join(root, "resources", "install-stamp.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return {"path": path, "sha256": None, "unreadable": True}
    return {
        "path": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "commit": data.get("commit") or data.get("gitCommit"),
        "branch": data.get("branch"),
        "builtAt": data.get("builtAt"),
        "source": data.get("source"),
    }


def read_runtime_facts(data_home: str | None) -> dict:
    """数据家的**结构事实**（不是内容）—— 判 bundled / pip 就看这几个点，报出来省得每次手查。"""
    if not data_home:
        return {}
    agent_tree = os.path.join(data_home, "plobi-agent")
    return {
        "data_home": data_home,
        "agent_tree_present": os.path.isdir(agent_tree),
        "agent_tree_has_git": os.path.exists(os.path.join(agent_tree, ".git")),
        "agent_tree_install_method_file": os.path.exists(os.path.join(agent_tree, ".install_method")),
        "home_install_method_file": os.path.exists(os.path.join(data_home, ".install_method")),
        "home_has_install_stamp": os.path.exists(os.path.join(data_home, "install-stamp.json")),
        "home_has_wheelhouse": os.path.isdir(os.path.join(data_home, "wheelhouse")),
    }


def snapshot(root: str, data_home: str | None, deep: bool = False) -> dict:
    tree = scan_tree(root, deep=deep)
    return {
        "schema": SCHEMA,
        "recorded_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "install_root": root,
        "install_stamp": read_install_stamp(root),
        "tree": tree,
        "runtime_facts": read_runtime_facts(data_home),
    }


# --------------------------------------------------------------------------- 基线读写

def baseline_path(state_dir: str) -> str:
    return os.path.join(state_dir, BASELINE_NAME)


def load_baseline(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_baseline(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- 比对

def diff_tree(old: dict, new: dict, limit: int) -> dict:
    old_files: dict = old.get("files") or {}
    new_files: dict = new.get("files") or {}
    added = sorted(set(new_files) - set(old_files))
    removed = sorted(set(old_files) - set(new_files))
    changed = sorted(k for k in set(old_files) & set(new_files) if old_files[k] != new_files[k])
    return {
        "added": added[:limit] if limit else added,
        "removed": removed[:limit] if limit else removed,
        "changed": changed[:limit] if limit else changed,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "deep_changed": [],
    }


def _fmt_ts(iso: str | None) -> str:
    return iso or "(未记)"


def format_diff(diff: dict, indent: str = "  ") -> list[str]:
    lines: list[str] = []
    labels = (("added", "新增"), ("removed", "删除"), ("changed", "改动"))
    for key, label in labels:
        total = diff[f"{key}_count"]
        if not total:
            continue
        lines.append(f"{indent}{label} {total} 个：")
        for path in diff[key]:
            lines.append(f"{indent}  {label} {path}")
        if total > len(diff[key]):
            lines.append(f"{indent}  …另有 {total - len(diff[key])} 个未列出（-v 看全）")
    return lines


# --------------------------------------------------------------------------- 子命令

def cmd_record(args) -> int:
    root, how = resolve_install_root(args.install_root)
    if not root:
        print(f"定位不到安装位：{how}", file=sys.stderr)
        return 2
    data_home, _ = resolve_data_home(args.data_home)
    state_dir, state_how = resolve_state_dir(args.state_dir, for_check=False)
    payload = snapshot(root, data_home, deep=args.deep)
    save_baseline(baseline_path(state_dir), payload)
    tree = payload["tree"]
    print(f"已记基线：{baseline_path(state_dir)}（落点来自 {state_how}）")
    print(f"  安装位（来自 {how}）：{root}")
    print(f"  文件 {tree['file_count']} 个 / {tree['total_bytes'] / 1e6:.1f} MB / 最新 mtime {_fmt_ts(tree['newest_mtime'])}")
    stamp = payload.get("install_stamp") or {}
    print(f"  install-stamp：commit {stamp.get('commit') or '(无)'} / branch {stamp.get('branch') or '(无)'} / builtAt {stamp.get('builtAt') or '(无)'}")
    if args.deep:
        print(f"  深摘要 {len(tree['deep'])} 个：{', '.join(sorted(tree['deep'])) or '(无)'}")
    return 0


def cmd_show(args) -> int:
    root, how = resolve_install_root(args.install_root)
    data_home, data_how = resolve_data_home(args.data_home)
    state_dir, state_how = resolve_state_dir(args.state_dir, for_check=True)
    base = load_baseline(baseline_path(state_dir)) if state_dir else None
    print(f"基线：{baseline_path(state_dir) if state_dir else '—'} —{'有' if base else '无'}")
    if not state_dir:
        print(f"  {state_how}")
    else:
        print(f"  落点来自 {state_how}")
    if base:
        tree = base.get("tree") or {}
        print(f"  记录于 {base.get('recorded_at')} / 安装位 {base.get('install_root')}")
        print(f"  文件 {tree.get('file_count')} 个 / {(tree.get('total_bytes') or 0) / 1e6:.1f} MB")
        stamp = base.get("install_stamp") or {}
        print(f"  stamp commit {stamp.get('commit') or '(无)'} builtAt {stamp.get('builtAt') or '(无)'}")
    print(f"现场：安装位 {'—' if not root else root}（{how}）")
    print(f"      数据家 {'—' if not data_home else data_home}（{data_how}）")
    facts = read_runtime_facts(data_home)
    if facts:
        print("      结构事实：" + json.dumps(facts, ensure_ascii=False))
    return 0


def cmd_check(args) -> int:
    root, how = resolve_install_root(args.install_root)
    if not root:
        print(f"定位不到安装位：{how}", file=sys.stderr)
        return 2
    data_home, _ = resolve_data_home(args.data_home)
    state_dir, state_how = resolve_state_dir(args.state_dir, for_check=True)
    if state_dir is None:
        print(f"还没有基线。{state_how}", file=sys.stderr)
        print("先跑一次 record（装完、确认干净的那一刻）。", file=sys.stderr)
        return 2
    path = baseline_path(state_dir)
    base = load_baseline(path)
    if not base:
        print(f"还没有基线（{path}）：先跑一次 record。", file=sys.stderr)
        return 2

    now = snapshot(root, data_home, deep=args.deep)
    limit = 0 if args.verbose else 30
    diff = diff_tree((base.get("tree") or {}), now["tree"], limit)
    old_stamp = base.get("install_stamp") or {}
    new_stamp = now.get("install_stamp") or {}
    stamp_moved = bool(old_stamp.get("sha256")) and old_stamp.get("sha256") != new_stamp.get("sha256")
    stamp_vanished = bool(old_stamp.get("sha256")) and not new_stamp.get("sha256")

    if args.deep:
        old_deep = (base.get("tree") or {}).get("deep") or {}
        new_deep = now["tree"].get("deep") or {}
        diff["deep_changed"] = sorted(k for k in set(old_deep) & set(new_deep) if old_deep[k] != new_deep[k])

    dirty = diff["added_count"] or diff["removed_count"] or diff["changed_count"] or diff["deep_changed"]

    if args.json:
        print(json.dumps({
            "verdict": "clean" if not dirty else ("reinstalled" if stamp_moved else "dirty"),
            "install_root": root,
            "baseline_recorded_at": base.get("recorded_at"),
            "stamp_moved": stamp_moved,
            "diff": diff,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"安装位：{root}（{how}）")
        print(f"基线：{path}（记录于 {base.get('recorded_at')}）")
        stamp_line = (f"  install-stamp：{old_stamp.get('commit') or '(无)'} → {new_stamp.get('commit') or '(无)'}"
                      f"（builtAt {old_stamp.get('builtAt') or '(无)'} → {new_stamp.get('builtAt') or '(无)'}）")
        if dirty:
            print(stamp_line)
            print(f"差异：新增 {diff['added_count']} / 删除 {diff['removed_count']} / 改动 {diff['changed_count']}")
            for line in format_diff(diff):
                print(line)
            for rel in diff["deep_changed"]:
                print(f"  内容变了（体积与 mtime 都没动）：{rel}")

    if not dirty:
        print("CLEAN — 安装位与基线一致，没有被动过。")
        return 0
    if stamp_moved:
        print("注意：install-stamp.json 换了 —— 这是安装包本身换了（发版/重装），不是手改安装位。")
        print("      复核这一版确实该换（看 builtAt / commit 对不对），然后重新 record；旧基线就此作废。")
        return 0
    if stamp_vanished:
        print("注意：install-stamp.json 不见了 —— 有人删了它，或这棵不是安装版。")
    print("DIRTY — 安装位被动过（stamp 没变，文件却不一样了）。")
    print("        纪律 §3.7：安装位对 Agent 只读，修 bug 只改源码仓。")
    print("        要么把改动撤回去，要么确认这是有意为之并重新 record。")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装位哨兵：核对 D:\\Software\\Plobi 有没有被手改（纪律 §3.7）")
    parser.add_argument("command", choices=("record", "check", "show"))
    parser.add_argument("--install-root", default=None, help="安装位根；默认 $PLOBI_INSTALL_ROOT → 注册表 DisplayIcon")
    parser.add_argument("--data-home", default=None, help="数据家；默认 $PLOBI_HOME → plobi_constants.get_plobi_home()")
    parser.add_argument("--state-dir", default=None, help="基线落点；默认 <data-home>/guard")
    parser.add_argument("--deep", action="store_true", help="对 exe / app.asar 补 sha256")
    parser.add_argument("--json", action="store_true", help="check 时输出 JSON")
    parser.add_argument("-v", "--verbose", action="store_true", help="差异全列，不截断")
    args = parser.parse_args(argv)
    return {"record": cmd_record, "check": cmd_check, "show": cmd_show}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

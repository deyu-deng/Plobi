#!/usr/bin/env python3
"""生成 scripts/repo_map.md —— 整仓代码关系网（WP-REPO-MAP / 裁定 70）。

 Python 半 = grimp 的模块级导入图，TS 半 = dependency-cruiser 的文件级图。

为什么还要自己跑一层 ast：本仓根有一批**不带 `__init__.py`** 的散 `.py`，而 grimp 的
`build_graph()` 扫描单位是「包」，所以这些文件作为**引用方**一条出边都扫不到——本仓最重的
几个入口（`cli.py` / `run_agent.py` / `model_tools.py`）在纯 grimp 图里会长成「只有人造它、
它不找人」的半截节点。`stray_out_edges()` 就是补这条缺口：口径与 grimp 无关（stdlib ast，
含函数体内 import），因此可以拿来当独立真值和 grimp 对账（见 tests/scripts/test_repo_map.py）。
入边不用补：`include_external_packages=True` 时这些名字已经作为节点存在，包内文件指向它们的
边都在。

产物红线（本刀的 `tests/scripts/test_repo_map.py` 逐条守着——该测试与闸门接线、TS 半同批，本轮未落地）：
* 字节稳定：不写时间戳、绝对盘符、机器名、包版本；路径一律仓内相对 + 正斜杠；
  排序键固定 `(入边降序, 出边降序, 名字升序)`，名字升序那个 tie-break 不能省。
* 体积封顶：≤ 400 行且 ≤ 20,000 B（跟 check_arch_gates.py 的指令文件上限同——防它长成
  第二份 AGENTS.md）。超了是块粒度选错，回去调块定义，不许靠截断糊。
* 图头自陈盲区：动态导入的处数由本文件当场数出来，不是抄的。

用法（`uv run --extra dev` 把 dev extra 里的 grimp 带进解释器）：

    uv run --extra dev python scripts/build_repo_map.py                     # 重生成
    uv run --extra dev python scripts/build_repo_map.py --check             # 闸门：只重算 Python 半
    uv run --extra dev python scripts/build_repo_map.py --check --with-ts   # 连 TS 半一起重算

缺依赖一律**报红并给修法**，不黄灯、不静默跳过（R-053 的教训：没跑过的门不能声称自己跑过）。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import chdir, contextmanager
from pathlib import Path

# ── 产物契约（行为契约，可以写死；边数块数是快照，不许写死）────────────────────────
MAX_LINES = 400
MAX_BYTES = 20_000
HOTSPOT_TOP = 5  # 任务书：每块 fan-in / fan-out 前 5 的文件

# 块粒度：TS 侧只认「成员」这一层，apps/desktop/src 的一级目录另算子块。
DESKTOP_SRC_PARENT = "apps/desktop"
TS_SUBBLOCK_DEPTH = 3  # apps/desktop/src/<这一段> 算一个子块

# TS 源文件扩展名（dependency-cruiser 认识的几种写法）。
TS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

# 生成物 / 第三方目录：既不进图，也不参与盲区计数。
PRUNE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "release",
        "coverage",
        "web_dist",
        "out",
    }
)

# 动态导入 = 静态图天生看不见的地方。头部数字的口径就是这三个模式。
DYNAMIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("importlib.import_module", re.compile(r"importlib\.import_module")),
    ("__import__(", re.compile(r"__import__\(")),
    ("importlib.resources", re.compile(r"importlib\.resources")),
)

REGION_PYTHON = ("meta", "py_overview", "py_hotspots")
REGION_TS = ("ts_overview", "ts_hotspots")
ALL_REGIONS = REGION_PYTHON + REGION_TS

ARTIFACT_RELPATH = "scripts/repo_map.md"
GENERATOR_RELPATH = "scripts/build_repo_map.py"


def repo_root() -> Path:
    """仓根。测试用 PLOBI_REPO_MAP_ROOT 指到临时树上（同一套代码，不改产物口径）。"""
    override = os.environ.get("PLOBI_REPO_MAP_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[1]


# ──────────────────────────────── Python 半 ────────────────────────────────


def package_roots(repo: Path) -> list[str]:
    """仓根带 `__init__.py` 的目录 = grimp 能扫的包根。"""
    return sorted(
        p.name
        for p in repo.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_")) and (p / "__init__.py").is_file()
    )


def stray_modules(repo: Path) -> list[str]:
    """仓根散 `.py` 的模块名（不带 `__init__.py` 的那一批，grimp 当引用方扫不到）。"""
    return sorted(p.stem for p in repo.glob("*.py"))


def _internal_roots(repo: Path) -> tuple[set[str], set[str]]:
    roots = set(package_roots(repo))
    strays = set(stray_modules(repo))
    return roots, strays


def truth_out_edges(repo: Path) -> dict[str, set[str]]:
    """ast 真值：仓根散模块各自 import 了哪些「仓内根」下的模块（含函数体内 import）。

    口径与 grimp 完全无关，所以它能当对账用的独立真值。相对导入不算（散模块在仓根，
    `from . import x` 无意义），只认 `import a.b.c` / `from a.b.c import x` 的 a.b.c。
    """
    roots, strays = _internal_roots(repo)
    internal = roots | strays
    out: dict[str, set[str]] = {}
    for name in sorted(strays):
        f = repo / f"{name}.py"
        targets: set[str] = set()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.module and not node.level:
                        names = [node.module]
                for dotted in names:
                    if dotted.split(".")[0] in internal:
                        targets.add(dotted)
        out[name] = targets
    return out


def dynamic_import_counts(repo: Path) -> dict[str, int]:
    """全仓 tracked `.py` 里，静态图看不见的动态导入有多少处（当场数，不抄）。

    用 `git grep` 数：范围就是「进了版本管理的 Python」，不受 .venv / node_modules /
    本地未跟踪垃圾影响，也能被测试用另一套实现（自己走目录 + 正则）独立复核。
    **排除本生成器自己**：它的源码里就写着这三个模式串（正则、标签、注释各一处），
    把它们数进「产品代码有多少处动态导入」既不是事实，还会让图件在「生成器进仓」那一笔
    自己变脏——计数器不该是它自己计量的对象。
    `git grep` 无命中时 exit 1（不是错误），exit ≥2 才是真失败。
    """
    counts: dict[str, int] = {}
    for label, pat in DYNAMIC_PATTERNS:
        proc = subprocess.run(
            [
                "git",
                "grep",
                "-I",
                "-o",
                "-E",
                pat.pattern,
                "--",
                "*.py",
                f":(exclude){GENERATOR_RELPATH}",
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode not in (0, 1):
            raise DependencyMissing(
                f"git grep 数 `{label}` 时失败（exit {proc.returncode}）：{proc.stderr.strip()[:200]}",
                "闸门要靠 git 数盲区：确认在仓内跑、且 git 可用",
            )
        counts[label] = sum(1 for line in proc.stdout.splitlines() if line.strip())
    return counts


@contextmanager
def _importable_repo(repo: Path):
    """grimp 的包定位走解释器的 sys.path，不看 cwd；临时把仓根插进去，用完立刻摘掉。

    仓根有 `cli.py` / `utils.py` / `setup.py` 这类会撞依赖名字的文件，所以这个窗口必须短，
    且只在建图那一下开着。
    """
    injected = str(repo) not in sys.path
    if injected:
        sys.path.insert(0, str(repo))
    try:
        with chdir(repo):
            yield
    finally:
        if injected:
            sys.path.remove(str(repo))


def grimp_view(repo: Path):
    """grimp 建图 + 散模块出边补齐，返回 (节点集合, 边集合, 对账用的分表)。

    边是模块级 `(importer, imported)`，两端都只保留仓内根（`tests` 也算仓内根——它是包）。
    """
    try:
        import grimp
    except ImportError as exc:  # pragma: no cover - 走的是「缺依赖即红」那条路
        raise DependencyMissing(
            "grimp 装不上",
            "uv sync --extra dev --locked   (import grimp 失败：" + type(exc).__name__ + ")",
        ) from exc

    roots = package_roots(repo)
    _, strays = _internal_roots(repo)
    internal = set(roots) | set(strays)

    with _importable_repo(repo):
        graph = grimp.build_graph(*roots, include_external_packages=True, cache_dir=None)

    edges: set[tuple[str, str]] = set()
    grimp_stray_out: dict[str, set[str]] = {name: set() for name in strays}
    for importer in graph.modules:
        if importer.split(".")[0] not in internal:
            continue
        try:
            imported = {t for t in graph.find_modules_directly_imported_by(importer) if t.split(".")[0] in internal}
        except Exception:
            imported = set()
        edges.update((importer, t) for t in imported)
        if importer in grimp_stray_out:
            grimp_stray_out[importer] |= imported

    truth = truth_out_edges(repo)
    for name, targets in truth.items():
        edges.update((name, t) for t in targets)

    nodes = {m for m in graph.modules if m.split(".")[0] in internal}
    nodes |= set(strays)
    for src, dst in edges:
        nodes.add(src)
        nodes.add(dst)
    return nodes, edges, truth, grimp_stray_out


# ───────────────────────────────── TS 半 ──────────────────────────────────


class DependencyMissing(RuntimeError):
    """缺依赖：报红并给修法，不许退化成黄灯。"""

    def __init__(self, what: str, fix: str) -> None:
        super().__init__(f"{what}\n  修法：{fix}")
        self.what = what
        self.fix = fix


def _node() -> str:
    node = os.environ.get("PLOBI_REPO_MAP_NODE", "").strip() or shutil.which("node") or ""
    if not node:
        raise DependencyMissing("找不到 node", "装 Node >= 20 并让它进 PATH")
    return node


def _node_modules(repo: Path) -> Path:
    """workspace 提升布局：依赖住在**仓库根** node_modules/，不在 apps/desktop 下（R-053 的坑）。"""
    override = os.environ.get("PLOBI_REPO_MAP_NODE_MODULES", "").strip()
    return Path(override) if override else repo / "node_modules"


def _ts_member_blocks(repo: Path) -> list[str]:
    """根 package.json 的 workspaces → TS 块（任务书定的四个成员各算一块）。

    规则：通配段（`apps/*`、`ui-tui/packages/*`）取通配符**左边那一层**再展开成它的子目录；
    `ui-tui/packages/plobi-ink` 因此归进父成员 `ui-tui`，不额外长出一块。
    """
    try:
        workspaces = json.loads((repo / "package.json").read_text(encoding="utf-8"))["workspaces"]
    except (OSError, ValueError, KeyError) as exc:
        raise DependencyMissing(f"读不到根 package.json 的 workspaces：{exc}", "检查根 package.json") from exc

    blocks: set[str] = set()
    for spec in sorted(workspaces):
        head, sep, tail = spec.partition("*")
        parent = head.rstrip("/")
        if sep:  # 带通配：展开通配符左边那一层的子目录
            if not parent or not (repo / parent).is_dir():
                continue
            for child in sorted((repo / parent).iterdir()):
                if child.is_dir() and child.name not in PRUNE_DIRS:
                    blocks.add((f"{parent}/" if parent else "") + child.name)
        elif parent:
            blocks.add(parent)
    # 嵌在别的成员底下的（`ui-tui/packages/plobi-ink`）归进祖先成员，不另长一块
    tops = sorted(blocks, key=lambda b: b.count("/"))
    kept: list[str] = []
    for block in tops:
        if not any(block == other or block.startswith(other + "/") for other in kept):
            kept.append(block)
    return sorted(b for b in kept if (repo / b).is_dir())


def _ts_entry_dirs(repo: Path, member: str) -> list[tuple[str, str]]:
    """成员里实际住着 TS 源的一级目录 + 扩展名，展开成 depcruise 的 glob。

    depcruise 的位置参数不吃裸目录名（`src` → 0 个节点），得给 glob；
    glob 也不能含二进制（`src/**/*` 会把 .woff2 喂给解析器然后整轮报错）。
    """
    base = repo / member
    pairs: set[tuple[str, str]] = set()
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = Path(dirpath).relative_to(base).as_posix()
        top = rel_dir.split("/")[0] if rel_dir != "." else ""
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS)
        if not top:
            continue
        for fn in filenames:
            ext = Path(fn).suffix
            if ext in TS_EXTS:
                pairs.add((top, ext))
    return sorted(pairs)


def _member_tsconfig(repo: Path, member: str) -> str | None:
    """Vite 系项目把 paths 写在 tsconfig.app.json（`web`），Electron 系写在 tsconfig.json。"""
    for name in ("tsconfig.app.json", "tsconfig.json"):
        if (repo / member / name).is_file():
            return name
    return None


def _alias_prefixes(repo: Path, member: str, tsconfig: str | None) -> tuple[str, ...]:
    """成员 tsconfig 里 `compilerOptions.paths` 的键（去掉尾部 `*`）= 真别名的前缀。

    tsconfig 带 `/* */` 注释（`web/tsconfig.app.json` 就靠注释分段），所以先剥块注释再解 JSON。
    有了前缀才能把 `@/store/session`（该被解析成仓内文件）和 `@assistant-ui/react`（本来就该走
    npm）分开——不然「别名一条都没解析成功」这道红线会把两者混成一团，也就抓不到真正的假绿
    （depcruise 没有 typescript 时，`@/x` 会被原样回显成 resolved 并标 couldNotResolve）。
    """
    if not tsconfig:
        return ()
    try:
        raw = (repo / member / tsconfig).read_text(encoding="utf-8")
    except OSError:
        return ()
    try:
        paths = json.loads(re.sub(r"/\*.*?\*/", "", raw, flags=re.S))["compilerOptions"]["paths"]
    except (ValueError, KeyError, TypeError):
        return ()
    return tuple(key.rstrip("*") for key in sorted(paths) if key.rstrip("*"))


def require_ts_dependencies(repo: Path) -> Path:
    """TS 半的依赖在不在。缺了就报红并给修法——照 `check_arch_gates.py:133-139` 的形状写。

    `--check` 只重算 Python 半时**也要**调它：不能因为「这次没跑 depcruise」就对缺依赖闭嘴，
    没跑过的门不能声称自己跑过（R-053）。`typescript` 不在这里查——它是功能性依赖，
    缺了 depcruise 照样 exit 0 但别名一条不解析，那种假绿在 depcruise_view 里按结果抓。
    """
    entry = _node_modules(repo) / "dependency-cruiser" / "bin" / "dependency-cruiser.mjs"
    if not entry.is_file():
        raise DependencyMissing(
            "仓库根 node_modules 里没有 dependency-cruiser",
            "在仓库根跑 `npm ci`（workspace 依赖提升到根目录，不在 apps/desktop 下）",
        )
    _node()
    return entry


def depcruise_view(repo: Path) -> tuple[set[str], set[tuple[str, str]], dict[str, int]]:
    """四个成员各跑一次 dependency-cruiser，返回 (文件节点, 边, 计数)。

    别名（`@/` → `src/`、`@plobi/shared` → `apps/shared/src/index.ts`）认不认得起来，取决于
    depcruise 能不能找到 `typescript` 包：找不到时它把 `@/x` 原样回显成 resolved 并标
    couldNotResolve —— 看着像跑过了，其实一条都没解析（本机实测：装了 typescript 之后
    `@/components/page-loader` 才解析成 `src/components/page-loader.tsx`）。所以这里除了看
    exit code，还要验 transpiler 可用性，以及「有 tsconfig 别名就不许一条都没解析成功」。
    """
    entry = require_ts_dependencies(repo)
    nm = _node_modules(repo)
    if not (nm / "typescript" / "package.json").is_file():
        raise DependencyMissing(
            "仓库根 node_modules 里没有 typescript（depcruise 解析 .tsx 与 tsconfig paths 要用它）",
            "在仓库根跑 `npm ci`（workspace 依赖提升到根目录，不在 apps/desktop 下）",
        )
    members = _ts_member_blocks(repo)
    nodes: set[str] = set()
    edges: set[tuple[str, str]] = set()
    stats = {"files": 0, "edges": 0, "alias_specifiers": 0, "alias_resolved": 0, "runs": 0}

    with tempfile.TemporaryDirectory(prefix="repo-map-dc-") as tmp:
        for member in members:
            globs = [f"{top}/**/*{ext}" for top, ext in _ts_entry_dirs(repo, member)]
            if not globs:
                continue
            options: dict = {"doNotFollow": {"path": "node_modules"}, "combinedDependencies": False}
            tsconfig = _member_tsconfig(repo, member)
            if tsconfig:
                options["tsConfig"] = {"fileName": tsconfig}
            prefixes = _alias_prefixes(repo, member, tsconfig)
            cfg = Path(tmp) / f"dc-{member.replace('/', '-')}.json"
            cfg.write_text(json.dumps({"options": options}), encoding="utf-8")
            proc = subprocess.run(
                [_node(), str(entry), "-c", str(cfg), "-T", "json", "-f", "-", *globs],
                cwd=str(repo / member),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"dependency-cruiser 在 {member} 上失败（exit {proc.returncode}）："
                    f"{proc.stderr.strip()[:300] or proc.stdout.strip()[:300]}"
                )
            try:
                payload = json.loads(proc.stdout)
            except ValueError as exc:
                raise RuntimeError(f"dependency-cruiser 在 {member} 上没吐出 JSON：{exc}") from exc

            transpilers = (payload.get("summary") or {}).get("environment", {}).get(
                "transpilersFound", []
            )
            if not any(t.get("name") == "typescript" and t.get("available") for t in transpilers):
                raise DependencyMissing(
                    f"depcruise 在 {member} 上没拿到 typescript transpiler，别名与 .tsx 会静默不解析",
                    "在仓库根跑 `npm ci`（workspace 依赖提升到根目录，不在 apps/desktop 下）",
                )

            for module in payload.get("modules", []):
                src = module.get("source") or ""
                if not src or src.startswith((".", "/")) or "node_modules" in src:
                    continue
                src_path = posixpath.normpath(posixpath.join(member, src))
                if not (repo / src_path).is_file():
                    continue
                nodes.add(src_path)
                for dep in module.get("dependencies") or []:
                    specifier = str(dep.get("module") or "")
                    is_alias = bool(prefixes) and specifier.startswith(prefixes)
                    types = dep.get("dependencyTypes") or []
                    if is_alias:
                        stats["alias_specifiers"] += 1
                    if "local" not in types or dep.get("couldNotResolve"):
                        continue
                    resolved = dep.get("resolved") or ""
                    if not resolved or "node_modules" in resolved:
                        continue
                    dst_path = posixpath.normpath(posixpath.join(member, resolved))
                    if dst_path.startswith("..") or not (repo / dst_path).is_file():
                        continue
                    edges.add((src_path, dst_path))
                    if is_alias:
                        stats["alias_resolved"] += 1
            stats["runs"] += 1

    stats["files"] = len(nodes)
    stats["edges"] = len(edges)
    if stats["alias_specifiers"] and not stats["alias_resolved"]:
        raise DependencyMissing(
            "depcruise 一个 tsconfig 别名都没解析出来（多半是 typescript/tsconfig 没就位）",
            "在仓库根跑 `npm ci`，再确认成员目录里有 tsconfig(.app).json 且带 compilerOptions.paths",
        )
    return nodes, edges, stats


# ────────────────────────────── 块聚合与渲染 ──────────────────────────────


def block_of_module(name: str, strays: set[str]) -> str:
    return name.split(".")[0]


def block_of_file(path: str, members: list[str]) -> str:
    for member in members:
        if path == member or path.startswith(member + "/"):
            return member
    return path.split("/")[0]


def subblock_of_file(path: str) -> str | None:
    """apps/desktop/src/<这一段> —— 只有 desktop 的 src 往下切一层。"""
    prefix = DESKTOP_SRC_PARENT + "/src/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix) :]
    if "/" not in rest:
        return None  # src 根上的散文件，留在父块
    return prefix + rest.split("/")[0]


def module_path(name: str, repo: Path) -> str:
    """模块点分名 → 仓内相对路径（落不到文件的就是命名空间目录，原样给个前缀）。"""
    stem = name.replace(".", "/")
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if (repo / candidate).is_file():
            return candidate
    return stem


def _display(name: str, block: str, repo: Path, kind: str = "py") -> str:
    """热点列表里显示成「块内相对路径」，省字节也让下钻的人一眼看懂。"""
    path = module_path(name, repo) if kind == "py" else name
    if path.startswith(block + "/"):
        path = path[len(block) + 1 :]
    return path


def _rank_key(row) -> tuple:
    """固定排序键：(入边降序, 出边降序, 名字升序)。tie-break 不能省，同分打乱顺序就不稳定了。"""
    return (-row["in"], -row["out"], row["name"])


def aggregate_python(repo: Path) -> dict:
    nodes, edges, truth, grimp_stray_out = grimp_view(repo)
    _, strays = _internal_roots(repo)
    stray_set = set(strays)

    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    block_pairs: dict[str, Counter[str]] = defaultdict(Counter)
    internal_edges: Counter[str] = Counter()
    for src, dst in edges:
        if src == dst:
            continue
        fan_out[src] += 1
        fan_in[dst] += 1
        b_src, b_dst = block_of_module(src, stray_set), block_of_module(dst, stray_set)
        if b_src == b_dst:
            internal_edges[b_src] += 1
        else:
            block_pairs[b_src][b_dst] += 1

    all_blocks = set(package_roots(repo)) | stray_set
    rows = []
    for block in all_blocks:
        members = {m for m in nodes if block_of_module(m, stray_set) == block}
        rows.append(
            {
                "name": block,
                "in": sum(fan_in[m] for m in members),
                "out": sum(fan_out[m] for m in members),
                "internal": internal_edges[block],
                "targets": block_pairs[block],
                "members": members,
                "fan_in": fan_in,
                "fan_out": fan_out,
            }
        )
    rows.sort(key=_rank_key)

    return {
        "rows": rows,
        "nodes": nodes,
        "edges": edges,
        "truth": truth,
        "grimp_stray_out": grimp_stray_out,
        "strays": stray_set,
        "counts": {
            "package_roots": len(package_roots(repo)),
            "stray_modules": len(stray_set),
            "modules": len(nodes),
            "edges": len(edges),
        },
    }


def aggregate_ts(repo: Path) -> dict:
    nodes, edges, stats = depcruise_view(repo)
    members = _ts_member_blocks(repo)
    present = [m for m in members if any(n == m or n.startswith(m + "/") for n in nodes)]

    fan_in: Counter[str] = Counter()
    fan_out: Counter[str] = Counter()
    for src, dst in edges:
        if src == dst:
            continue
        fan_out[src] += 1
        fan_in[dst] += 1

    def build_rows(assign) -> list[dict]:
        grouped: dict[str, set[str]] = defaultdict(set)
        for n in nodes:
            grouped[assign(n)].add(n)
        pairs: dict[str, Counter[str]] = defaultdict(Counter)
        internal: Counter[str] = Counter()
        for src, dst in edges:
            b_src, b_dst = assign(src), assign(dst)
            if b_src == b_dst:
                internal[b_src] += 1
            else:
                pairs[b_src][b_dst] += 1
        rows = []
        for block, files in grouped.items():
            rows.append(
                {
                    "name": block,
                    "in": sum(fan_in[f] for f in files),
                    "out": sum(fan_out[f] for f in files),
                    "internal": internal[block],
                    "targets": pairs[block],
                    "members": files,
                    "fan_in": fan_in,
                    "fan_out": fan_out,
                }
            )
        rows.sort(key=_rank_key)
        return rows

    file_rows = build_rows(lambda p: p)
    sub_rows = [r for r in build_rows(lambda p: subblock_of_file(p) or block_of_file(p, members))]
    sub_rows = [r for r in sub_rows if r["name"].startswith(DESKTOP_SRC_PARENT + "/src/")]
    return {
        "rows": build_rows(lambda p: block_of_file(p, members)),
        "sub_rows": sub_rows,
        "nodes": nodes,
        "edges": edges,
        "members": present,
        "stats": stats,
        "top_files": sorted(file_rows, key=_rank_key)[:3],
    }


def render_python_regions(repo: Path, py: dict, blind: dict[str, int]) -> dict[str, list[str]]:
    regions: dict[str, list[str]] = {}

    shadowed = sorted(
        name
        for name in py["strays"]
        if (repo / name / "__init__.py").is_file()
    )
    meta = [
        "- 块 = 仓根带 `__init__.py` 的包根 + 仓根不带它的散 `.py`，各算一块；"
        f"包根 {py['counts']['package_roots']} + 散模块 {py['counts']['stray_modules']}，"
        f"撞名合并后 {len(py['rows'])} 块。模块节点 {py['counts']['modules']}，模块级边 {py['counts']['edges']}。",
        "  散模块的**出边**由本生成器跑一层 ast 补（grimp 只扫包，扫不到它们当引用方）；入边取 grimp 视图。",
        "- `入 / 出` = 指向本块 / 本块指出的边数（含块内部）；`内` = 其中两端同块的那部分。",
        "  热点按含块内的总边数排；**只有一个文件的块不列热点**（概览行就是它的全部）。",
        "- 盲区（静态图天生看不见；处数当场数，范围 = 全仓 tracked `.py` 再减本生成器自己——"
        "计数器不数自己，否则本文件进仓那一笔就会把图件弄脏；用 `git grep`）：",
    ]
    for label in sorted(blind):
        meta.append(f"  - `{label}` {blind[label]} 处")
    if shadowed:
        meta.append(
            "  - 名字与包根撞车的仓根散模块：" + ", ".join(shadowed)
            + "（`import` 时包赢，图里并成一个节点；这些文件本身只能当入口脚本跑）"
        )
    meta.append("  - 动态 `import` / 运行时拼出来的模块名 / 注册表驱动的分发，一律看不见——影响面下钻交给 Serena。")
    regions["meta"] = meta
    regions["py_overview"] = _table(py["rows"], repo)
    regions["py_hotspots"] = _hotspot_lines(py["rows"], repo, "py")
    return regions


def _table(rows: list[dict], repo: Path, with_targets: bool = True) -> list[str]:
    out = [
        "| 块 | 入 | 出 | 内 |" + (" 主要指向（块，边数） |" if with_targets else ""),
        "|---|---:|---:|---:|" + ("---|" if with_targets else ""),
    ]
    for row in rows:
        cells = [row["name"], str(row["in"]), str(row["out"]), str(row["internal"])]
        if with_targets:
            targets = ", ".join(
                f"{name} {count}"
                for name, count in sorted(row["targets"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            )
            cells.append(targets or "—")
        out.append("| " + " | ".join(cells) + " |")
    return out


def _hotspot_lines(rows: list[dict], repo: Path, kind: str, top: int = HOTSPOT_TOP) -> list[str]:
    """每块 fan-in / fan-out 前 N 的文件；单文件块不列（概览行就是它的全部）。"""
    lines: list[str] = []
    for row in rows:
        if len(row["members"]) <= 1:
            continue
        ins = sorted((f for f in row["members"] if row["fan_in"][f]), key=lambda f: (-row["fan_in"][f], f))[:top]
        outs = sorted(
            (f for f in row["members"] if row["fan_out"][f]), key=lambda f: (-row["fan_out"][f], f)
        )[:top]
        if not ins and not outs:
            continue
        lines.append(f"**{row['name']}**")
        if ins:
            lines.append("  入顶 " + " · ".join(f"{_display(f, row['name'], repo, kind)} {row['fan_in'][f]}" for f in ins))
        if outs:
            lines.append("  出顶 " + " · ".join(f"{_display(f, row['name'], repo, kind)} {row['fan_out'][f]}" for f in outs))
    return lines


def render_ts_regions(repo: Path, ts: dict) -> dict[str, list[str]]:
    overview = _table(ts["rows"], repo)
    overview += [
        "",
        f"成员块的文件节点 {ts['stats']['files']}，文件级边 {ts['stats']['edges']}；跑过 {ts['stats']['runs']} 个成员。",
        f"tsconfig 别名说明符 {ts['stats']['alias_specifiers']} 条，解析到仓内文件 {ts['stats']['alias_resolved']} 条"
        "（一个都没解析成 = depcruise 没拿到 typescript，那段是假的，生成器会直接报红）。",
        "子块 = `apps/desktop/src` 的一级目录，是对 `apps/desktop` 那一行再切一刀，不是并列的第 N 块。",
        "",
    ]
    return {
        "ts_overview": overview + _table(ts["sub_rows"], repo, with_targets=False),
        "ts_hotspots": _hotspot_lines(ts["rows"], repo, "ts") + _hotspot_lines(ts["sub_rows"], repo, "ts"),
    }


HEADER_LINES = [
    "# 仓内关系网（自动生成 · 勿手改 · 陈旧即红）",
    "",
    f"生成器：`{GENERATOR_RELPATH}` · 重生成：`uv run --extra dev python {GENERATOR_RELPATH}`",
    "校验：`scripts/check_arch_gates.py` 每次提交重算 Python 半并逐字节比对；TS 半要显式 `--with-ts`（原因见生成器 docstring 旁边的注释）。",
    "",
    "**覆盖范围**：只覆盖 `Code` 仓内根，不含 `$MIND_ROOT` 那个独立 vault。",
    "路径都是仓内相对 + 正斜杠；块按「入边降序，出边降序，名字升序」排。",
]

# 不带 --with-ts 时图里只有 Python 侧。这段占位不是装饰：读图的人要是以为这 27 块就是整仓，
# 就会拿着半张图判断影响面。等 TS 半真接进来了，这条路径产出的是真 ts_overview/ts_hotspots，
# 占位段自动消失（别手改产物，改这里）。
TS_PENDING_LINES = [
    "本段是**占位**：四个 workspace 成员（`apps/desktop`、`apps/shared`、`ui-tui`、`web`）"
    "与 `apps/desktop/src` 一级子块的 dependency-cruiser 图**还没并进来**（待第二块，"
    f"跑 `{GENERATOR_RELPATH} --with-ts` 才会生成这两段）。",
    "所以本图现在只等于 Python 半：前端那半在这里一条边都没有，别读成「前端谁都不依赖」。",
    "同为第二块的还有闸门接线：上面那句「每次提交重算并逐字节比对」是这一刀接线后的契约，"
    "在 `check_arch_gates.py` 真调用本生成器之前，本图不由任何钩子校验，改代码不会自动变红。",
]

SECTION_TITLES = {
    "meta": "口径与盲区自陈",
    "py_overview": "Python 块级概览",
    "py_hotspots": "Python 块内热点（fan-in / fan-out 前 5）",
    "ts_overview": "TS 块级概览（workspace 成员 + desktop src 子块）",
    "ts_hotspots": "TS 块内热点",
    "ts_pending": "TS 块级概览与热点（本轮留空 · 待第二块）",
}


def compose(regions: dict[str, list[str]], order: list[str]) -> str:
    out: list[str] = []
    out.extend(HEADER_LINES)
    out.append("")
    for name in order:
        lines = regions.get(name)
        if lines is None:
            continue
        out.append(f"<!-- BEGIN {name} -->")
        out.append(f"## {SECTION_TITLES[name]}")
        out.append("")
        out.extend(lines)
        out.append(f"<!-- END {name} -->")
        out.append("")
    text = "\n".join(out).rstrip("\n") + "\n"
    if "\\" in text:
        raise RuntimeError("产物里出现了反斜杠（绝对盘符/Windows 路径泄漏）——字节稳定红线")
    enforce_size_cap(text)
    return text


def enforce_size_cap(text: str) -> None:
    """封顶是行为契约，不是愿望：超了就报错，逼回去调块粒度，不许靠截断糊。

    现在只有 Python 半（约百行），等 TS 半并进来才是压力所在——真超了这道 raise 会当场说清楚
    「粒度选错了」，而不是悄悄吐一份塞不进上下文的图。
    """
    n_lines = len(text.splitlines())
    n_bytes = len(text.encode("utf-8"))
    if n_lines > MAX_LINES or n_bytes > MAX_BYTES:
        raise RuntimeError(
            f"产物 {n_lines} 行 / {n_bytes:,} B 超过封顶 {MAX_LINES} 行 / {MAX_BYTES:,} B。"
            "这是块粒度选错了（任务书必做 2），回去调粒度——不许截断。"
        )


def build(repo: Path, with_ts: bool) -> tuple[str, dict]:
    py = aggregate_python(repo)
    blind = dynamic_import_counts(repo)
    regions = render_python_regions(repo, py, blind)
    order = list(REGION_PYTHON)
    if with_ts:
        ts = aggregate_ts(repo)
        regions.update(render_ts_regions(repo, ts))
        order += list(REGION_TS)
    else:
        regions["ts_pending"] = list(TS_PENDING_LINES)
        order.append("ts_pending")
    return compose(regions, order), {"python": py, "ts": None if not with_ts else ts, "blind": blind}


# ──────────────────────────────── 落盘与校验 ────────────────────────────────


def _artifact_to_compare(repo: Path, against: str | None) -> tuple[str, str]:
    """取「要拿去比的那份」：`--against` 指定 > 索引 blob > HEAD blob > 工作树。

    闸门比的是**索引**那份——工作树里刚重生成的那份拿 HEAD 去比，会让每一次带图件的提交
    自己顶死自己（跟 check_arch_gates.py 的体积棘轮同一个道理：比 `git show :<路径>`）。
    `--against` 是给测试和 CI 的接缝：拿一份副本改一位数字，就能验「陈旧即红」，
    不用去动共享索引（这台机器上有多个会话同时在这份仓里提交）。
    """
    if against:
        path = Path(against)
        if not path.is_file():
            raise DependencyMissing(f"--against 指向的文件不存在：{path}", "指一份真实的图件副本")
        return path.read_text(encoding="utf-8"), f"--against {path.name}"
    for source, cmd in (
        ("index", ["show", f":{ARTIFACT_RELPATH}"]),
        ("HEAD", ["show", f"HEAD:{ARTIFACT_RELPATH}"]),
    ):
        proc = subprocess.run(["git", *cmd], cwd=str(repo), capture_output=True)
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8"), source
    path = repo / ARTIFACT_RELPATH
    if path.is_file():
        return path.read_text(encoding="utf-8"), "worktree"
    raise DependencyMissing(
        f"{ARTIFACT_RELPATH} 不存在（索引、HEAD、工作树都没有）",
        f"跑 `uv run --extra dev python {GENERATOR_RELPATH} --with-ts` 生成它，然后 git add -- {ARTIFACT_RELPATH}",
    )


def _region_text(text: str, name: str) -> str | None:
    begin, end = f"<!-- BEGIN {name} -->\n", f"\n<!-- END {name} -->"
    start = text.find(begin)
    if start < 0:
        return None
    stop = text.find(end, start)
    if stop < 0:
        return None
    return text[start + len(begin) : stop]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check(repo: Path, with_ts: bool, against: str | None = None) -> int:
    """重算到内存，与提交件逐字节比。陈旧 → 红。

    闸门默认只重算 **Python 半**，TS 半留显式 `--with-ts`。这不是偷懒，是预算：本机
    2026-10-07 实测 `--stdout`（Python 半）2.3 s，`--with-ts` 18.3 s——TS 那 16 s 全花在
    dependency-cruiser 上（四个成员各起一个 node 进程解析 `.tsx`，加了 typescript 之后
    单 apps/desktop 就要 20 s 量级）。任务书给的门槛是「每次闸门 ~5 s 以内」，塞不进。
    所以 Python 半每次提交都验，TS 半改代码的人显式跑一次（`--with-ts`）再提交图件；
    缺 dependency-cruiser 这类**依赖**问题照样每次报红，不退成黄灯。
    缺依赖不许静默：Python 半在 build() 里 import grimp，TS 半的依赖在这里显式探测。
    """
    require_ts_dependencies(repo)
    names = list(REGION_PYTHON) + (list(REGION_TS) if with_ts else [])
    fresh, _ = build(repo, with_ts=with_ts)
    try:
        old, source = _artifact_to_compare(repo, against)
    except DependencyMissing as exc:
        print(f"REPO-MAP GATE FAILED — {exc}", file=sys.stderr)
        return 1

    stale = []
    for name in names:
        a, b = _region_text(fresh, name), _region_text(old, name)
        if a is None or b is None:
            stale.append(f"{name}(缺段)")
        elif sha256(a) != sha256(b):
            stale.append(name)
    if stale:
        print(
            f"REPO-MAP GATE FAILED — {ARTIFACT_RELPATH} 与当前代码不一致（比的是 {source} 那份）：\n"
            f"  陈旧段：{', '.join(stale)}\n"
            f"  修法：uv run --extra dev python {GENERATOR_RELPATH} --with-ts\n"
            f"  然后 git add -- {ARTIFACT_RELPATH}",
            file=sys.stderr,
        )
        return 1
    print(f"repo map check passed（{len(names)} 段与 {source} 逐字节相同）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="重算并与提交件比对（陈旧即红）")
    parser.add_argument(
        "--with-ts",
        action="store_true",
        help="连 TS 半一起重算/校验（每个成员跑一次 dependency-cruiser，慢，闸门默认不带）",
    )
    parser.add_argument("--against", help="改与指定文件比对（测试与 CI 的接缝，不碰共享索引）")
    parser.add_argument("--stdout", action="store_true", help="写到标准输出，不落文件（测试用）")
    args = parser.parse_args(argv)

    repo = repo_root()
    try:
        if args.check:
            return check(repo, with_ts=args.with_ts, against=args.against)
        text, _ = build(repo, with_ts=args.with_ts)
    except DependencyMissing as exc:
        print(f"REPO-MAP FAILED — {exc}", file=sys.stderr)
        return 1
    if args.stdout:
        sys.stdout.write(text)
        return 0
    out_path = repo / ARTIFACT_RELPATH
    out_path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {ARTIFACT_RELPATH}: {len(text.splitlines())} 行 / {len(text.encode('utf-8')):,} B")
    return 0



if __name__ == "__main__":
    sys.exit(main())

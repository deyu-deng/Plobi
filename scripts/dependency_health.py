#!/usr/bin/env python3
"""Dependency health: which known-vulnerable pins can actually be moved, and which
"security pins" have quietly rotted.

Two questions this answers, which no off-the-shelf tool answers together:

  1. For every pinned dependency that has a known advisory, can we actually upgrade it?
       A  nothing blocks it            -> `uv lock -P <pkg>` moves it, no file edits
       B  only our own `==` blocks it  -> edit that pin (reports every line it appears on)
       C  someone else's ceiling       -> names the parent and the specifier that blocks it
       D  our ceiling's stated reason is unverifiable -> needs a human

  2. A line like `"cryptography==46.0.7",  # CVE-2026-39892` asserts the pin dodges
     those advisories. Pins age. If the pinned version is *itself* now flagged, the
     comment reads "handled" while the dependency is exposed — the worst kind of stale.

Advisory data comes from pip-audit (PyPA), which queries OSV + PyPI advisory sources —
deliberately NOT a hand-rolled advisory client. Any lookup failure is reported loudly and
makes the run fail; a security check that passes silently on error is worse than none.

Usage:
    uv run --with pip-audit --with packaging python scripts/dependency_health.py [--json]
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"

# Any quoted dependency spec, anywhere on a line, including extras:  "name[extra]==1.2.3"
DEP_RE = re.compile(r'["\'](?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)((?P<extras>\[[^\]]*\])?(?P<spec>[=<>!~][^"\']*))[\'"]')
IDS_RE = re.compile(r'(CVE-\d{4}-\d{4,7}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})')


def norm(s: str) -> str:
    return s.lower().replace('_', '-')


def run(cmd, ok_codes=(0,), **kw):
    """Run a command. `ok_codes` lists acceptable exits — pip-audit returns 1 when it
    simply *found* vulnerabilities, which is a normal outcome, not a failure."""
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode not in ok_codes:
        raise RuntimeError(f'命令失败(rc={p.returncode}): {" ".join(str(c) for c in cmd)}\n'
                           f'{(p.stderr or p.stdout).strip()[:800]}')
    return p.stdout


def audit() -> dict[str, dict]:
    """package -> {version, count, fix} from pip-audit over the full locked graph."""
    with tempfile.NamedTemporaryFile('r', suffix='.txt', delete=False) as fh:
        reqs = Path(fh.name)
    try:
        run(['uv', 'export', '--frozen', '--no-hashes', '--no-emit-project',
             '--all-extras', '-o', str(reqs)], cwd=ROOT)
        out = run(['pip-audit', '-r', str(reqs), '--no-deps', '--disable-pip',
                   '--progress-spinner', 'off', '-f', 'json'], ok_codes=(0, 1), cwd=ROOT)
    finally:
        reqs.unlink(missing_ok=True)
    start = out.find('{')
    if start < 0:
        raise RuntimeError(f'pip-audit 没有输出 JSON：{out[:400]}')
    data = json.loads(out[start:])
    res = {}
    for e in data.get('dependencies', []):
        vulns = e.get('vulns') or []
        if not vulns:
            continue
        fixes = {f for v in vulns for f in (v.get('fix_versions') or [])}
        res[norm(e['name'])] = {
            'name': e['name'], 'version': e['version'], 'count': len(vulns),
            'fix': sorted(fixes)[-1] if fixes else None,
            'ids': sorted({v['id'] for v in vulns} | {a for v in vulns for a in (v.get('aliases') or [])}),
        }
    return res


def cited_ids_by_package() -> dict[str, list[str]]:
    """CVE/GHSA ids cited in a line's trailing comment, attributed to the right package.

    Comments come in two shapes and must not be conflated:
        "cryptography==46.0.7",  # CVE-2026-39892            -> one dep on the line, binds to it
        [...]  # starlette: CVE-2026-48710; setuptools: <82  -> ids bind to the name in
                                                                THEIR OWN `;` segment only
    Attributing across the whole comment marks setuptools as a security pin it never claimed.
    """
    out: dict[str, list[str]] = {}
    for line in PYPROJECT.read_text(encoding='utf-8').splitlines():
        code, _, comment = line.partition('#')
        if not comment:
            continue
        deps = [m.group('name') for m in DEP_RE.finditer(code)]
        if not deps:
            continue
        for segment in comment.split(';'):
            ids = IDS_RE.findall(segment)
            if not ids:
                continue
            named = [d for d in deps if re.search(r'\b' + re.escape(d) + r'\b', segment, re.I)]
            targets = named if named else (deps if len(deps) == 1 else [])
            for d in targets:
                out.setdefault(norm(d), []).extend(ids)
    return {k: sorted(set(v)) for k, v in out.items()}


def reverse_deps() -> dict[str, list[str]]:
    """package -> which locked packages depend on it."""
    text = LOCK.read_text(encoding='utf-8')
    parents: dict[str, set] = {}
    for block in re.split(r'^\[\[package\]\]$', text, flags=re.M)[1:]:
        nm = re.search(r'^name = "(.+?)"', block, flags=re.M)
        if not nm:
            continue
        for d in re.findall(r'\{ name = "([^"]+)"', block):
            parents.setdefault(norm(d), set()).add(nm.group(1))
    return {k: sorted(v) for k, v in parents.items()}


@functools.lru_cache(maxsize=None)
def _requires_dist(parent: str, pver: str) -> tuple[str, ...]:
    import urllib.parse
    import urllib.request
    url = f'https://pypi.org/pypi/{urllib.parse.quote(parent)}/{pver}/json'
    req = urllib.request.Request(url, headers={'User-Agent': 'plobi-dependency-health'})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return tuple((json.load(r)['info'].get('requires_dist') or []))
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f'查 {parent}=={pver} 连续 4 次失败：{type(last).__name__}')


def parent_specifiers(parent: str, child: str) -> list[tuple[str, str, str | None]]:
    """What `parent` (at its LOCKED version) requires of `child`.

    Returns (parent, parent_version, specifier_or_None). Returns [] rather than a
    placeholder when there is genuinely no constraint — callers must not turn
    "no constraint stated" into a blocker.
    """
    text = LOCK.read_text(encoding='utf-8')
    m = re.search(r'^name = "' + re.escape(parent) + r'"\nversion = "(.+?)"', text, flags=re.M)
    if not m:
        return [(parent, '?', None)]
    pver = m.group(1)
    out = []
    for rd in _requires_dist(parent, pver):
        mm = re.match(r'^([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*([^;]*)', rd)
        if mm and norm(mm.group(1)) == norm(child):
            spec = mm.group(3).strip()
            out.append((parent, pver, spec or None))
    return out


def our_constraints(pkg: str) -> list[tuple[int, str]]:
    """Every line in pyproject.toml that constrains this package (any operator, not just ==)."""
    hits = []
    for lineno, line in enumerate(PYPROJECT.read_text(encoding='utf-8').splitlines(), 1):
        code = line.partition('#')[0]
        for m in DEP_RE.finditer(code):
            if norm(m.group('name')) == norm(pkg):
                hits.append((lineno, (m.group('spec') or '').strip()))
    return hits


def uv_can_move(pkg: str) -> tuple[bool, str]:
    p = subprocess.run(['uv', 'lock', '--dry-run', '--upgrade-package', pkg],
                       capture_output=True, text=True, cwd=ROOT)
    # uv writes its resolution report to stderr, not stdout.
    out = (p.stdout or '') + '\n' + (p.stderr or '')
    if p.returncode != 0:
        raise RuntimeError(f'uv lock --dry-run {pkg} 失败(rc={p.returncode}):\n{out[:400]}')
    for line in out.splitlines():
        if line.startswith('Update') and re.search(r'(^|\s)' + re.escape(pkg) + r'\s+v', line):
            return True, line.replace('Update ', '').strip()
    if 'No lockfile changes detected' in out:
        return False, 'uv 未改动（被 == 精确钉死，或父级上限卡住）'
    raise RuntimeError(f'uv lock --dry-run 对 {pkg} 输出无法识别:\n{out[:400]}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    vuln = audit()
    cited = cited_ids_by_package()
    rdeps = reverse_deps()

    groups: dict[str, list[dict]] = {'A': [], 'B': [], 'C': [], 'D': []}
    stale_pins: list[dict] = []
    errors: list[str] = []

    for key, info in sorted(vuln.items()):
        ours = our_constraints(info['name'])
        exact_lines = [ln for ln, spec in ours if spec.startswith('==')]
        try:
            movable, detail = uv_can_move(info['name'])
        except Exception as e:
            errors.append(str(e)); continue

        blockers: list[str] = []
        if not movable:
            for p in rdeps.get(key, []):
                if norm(p) == 'plobi-agent':
                    continue
                try:
                    specs = parent_specifiers(p, info['name'])
                except Exception as e:
                    errors.append(str(e)); continue
                for parent, pver, spec in specs:
                    if not spec:
                        continue          # no constraint stated -> not a blocker
                    if info['fix'] and Version_ok(info['fix'], spec):
                        continue
                    blockers.append(f'{parent}=={pver} 要求 {spec}  → 挡住修复版 {info["fix"]}')
        if blockers:
            g = 'C'
        elif exact_lines:
            g = 'B'
        elif movable:
            g = 'A'
        else:
            g = 'D'
        rec = {'package': info['name'], 'version': info['version'], 'vulns': info['count'],
               'fix': info['fix'], 'group': g, 'lines': exact_lines, 'our_specs': ours,
               'detail': detail, 'blockers': blockers,
               'cited_cves': cited.get(key, [])}
        groups[g].append(rec)
        if exact_lines and cited.get(key):
            stale_pins.append(rec)

    def emit():
        if args.json:
            print(json.dumps({'groups': groups, 'stale_security_pins': stale_pins,
                              'errors': errors}, indent=2, ensure_ascii=False))
            return
        for g, title in [('A', 'A 不改任何声明，uv 实测能自动升'),
                         ('B', 'B 只被你们自己的 == 钉住，改一行即可'),
                         ('C', 'C 被别的包的上限卡住，要连带升'),
                         ('D', 'D 原因未明，需要人看')]:
            rows = groups[g]
            print(f'\n=== {title}   {len(rows)} 个包 / {sum(r["vulns"] for r in rows)} 条 ===')
            for r in sorted(rows, key=lambda x: -x['vulns']):
                loc = ''
                if r['our_specs']:
                    loc = '  (pyproject: ' + '; '.join(f'第{ln} 行 {sp}' for ln, sp in r['our_specs']) + ')'
                print(f'  {r["package"]:20s} {r["version"]:10s} → {str(r["fix"]):10s} {r["vulns"]:2d} 条{loc}')
                for b in r['blockers'][:3]:
                    print(f'        · {b}')
        print(f'\n=== 失效的"安全钉死"   {len(stale_pins)} 处 ===')
        for r in stale_pins:
            print(f'  {r["package"]}=={r["version"]}  注释称已躲 {", ".join(r["cited_cves"][:4])}'
                  f'  →  该版本现被 {r["vulns"]} 条已知漏洞命中')

    emit()
    total = sum(len(v) for v in groups.values())
    if total != len(vuln):
        errors.append(f'分类数 {total} != 漏洞包数 {len(vuln)}')
    if errors:
        print(f'\n::error::有 {len(errors)} 处查询失败，本报告不完整，别当成"已排查干净"')
        for e in errors[:10]:
            print('  ' + e.replace('\n', ' ')[:220])
        # Red means the instrument broke — never red merely because there is work to do.
        return 2

    for g in ('C', 'D'):
        for r in groups[g]:
            print(f'::warning title=依赖 {r["package"]} 升不动::'
                  f'{r["vulns"]} 条已知漏洞，修复版 {r["fix"]}；'
                  f'{r["blockers"][0] if r["blockers"] else "原因未明，需人工看"}')
    for r in stale_pins:
        print(f'::warning title=失效的安全钉死::{r["package"]}=={r["version"]} '
              f'注释称已躲 {", ".join(r["cited_cves"][:3])}，但该版本现被 {r["vulns"]} 条已知漏洞命中')
    if not stale_pins and not groups['C'] and not groups['D']:
        print('\n没有需要人拍板的项。')
    return 0


def Version_ok(candidate: str, specifier: str) -> bool:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    return Version(candidate) in SpecifierSet(specifier)


if __name__ == '__main__':
    sys.exit(main())

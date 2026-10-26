#!/usr/bin/env python3
"""屏幕上会出现的每一个状态词，四语目录里都得有一句人话，而且那句话不许要求他知道文件名。

存在的理由（裁定 203 第 4 条，用户 10-25「你按最好的方案来，不要怕工作量」）：这一族坏法不是
"少翻译"，是**指引断供**——`no_credential`（没配账密）、`no_path_configured`（没指路径）这类
他自己就能修的码，目录里没句子时屏幕上统一落「暂未适配」，下一步就断了。这种事靠人记不住，
所以做成门：接进 `scripts/git-hooks/pre-commit`，缺句子即红。

**"会到屏幕上的码"是怎么算出来的**（这是上一版一次性脚本踩过的坑，别再踩一遍）：
- `status_of()` 每一个 return 里第二个位置的字面量；
- 它会原样透传的那两个状态集合（`_ERROR_STATES`／`_CONNECTED_STATES`）里的字面量；
- 每条信源 `probe()` 那只手回的 `code` 字面量（抽屉与行上都会按它查目录）。
上一版是拿正则全文扫 `key: value`，于是把 `note_for` 里那句英文中的 `obtained:` 当成一个键，
报出一堆假缺口——门要是会冤枉人，第三次就没人看它了（R-050）。这里走 AST + 行首锚定的解析。

另外两类只**出声不判红**：各采集器自己的 `STATE_*` 常量（其中有些确实一个都到不了屏幕，
逼着写四语句子就是造没人看的死句子），以及目录里写了却没人产出的码。

退出码：0＝绿；1＝有红。`--json` 给机器读。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
COLLECTORS = ROOT / "plobi" / "collectors"
API = COLLECTORS / "sources_api.py"
LANGS = ("zh", "en", "ja", "zh-hant")
I18N = ROOT / "apps" / "desktop" / "src" / "i18n"

#: 屏幕上的句子不许出现这四样（裁定 203 第 1 条）：他知道名字才能修，等于没指路。
ARTIFACTS = (
    ("环境变量名", re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){1,}\b")),
    ("命令行/代码片段（反引号）", re.compile(r"`[^`]+`")),
    (".env 那类文件名", re.compile(r"\.env\b|\bdb\b\.|\bagenda\.db\b|pool\.db", re.I)),
    ("盘符路径", re.compile(r"[A-Za-z]:[\\/]")),
)


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def screen_codes(api_path: Path = API, collectors_dir: Path = COLLECTORS) -> set[str]:
    """`status_of` 会交给界面的那一格的闭包：它自己 return 的 + 它透传的两个集合里的。"""
    tree = ast.parse(api_path.read_text(encoding="utf-8"))
    codes: set[str] = set()

    for node in tree.body:
        # `_ERROR_STATES = frozenset({...})` / `_CONNECTED_STATES = frozenset({...})`
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id.endswith("_STATES") for t in node.targets):
            codes |= {s for s in _strings(node.value) if _looks_like_code(s)}

        # `def status_of(...)` 里每个 return 的第二个字面量
        if isinstance(node, ast.FunctionDef) and node.name == "status_of":
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple):
                    for element in ret.value.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            codes.add(element.value)

    for py in sorted(collectors_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            # 探针那一只手：`return {"ready": ..., "code": "key_missing", ...}`
            if isinstance(node, ast.Dict):
                ready_true = any(isinstance(k, ast.Constant) and k.value == "ready" and isinstance(v, ast.Constant)
                                 and v.value is True for k, v in zip(node.keys, node.values))
                for key, value in zip(node.keys, node.values):
                    if ready_true:
                        continue     # `ready: True` 那一支的码不会成为 `status_code`（status_of 只在前提交不成立时查它）
                    if isinstance(key, ast.Constant) and key.value == "code" \
                            and isinstance(value, ast.Constant) and isinstance(value.value, str):
                        codes.add(value.value)
            # 探针交出去的码必须**写成一格字面量**（`PROBE_CODES`），门才数得出来：
            # 只写变量名（`{"code": state}`）的话，屏幕上真会出现的一句却没有句子。
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "PROBE_CODES" for t in node.targets):
                codes |= {s for s in _strings(node.value) if _looks_like_code(s) and s != "ready"}
    return {c for c in codes if _looks_like_code(c)}


def _looks_like_code(word: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{2,40}", word))


def _block_after(text: str, marker: str) -> str:
    """取 `marker` 之后那一对花括号**里**的内容（配对到它自己的闭括号，不多拿兄弟格）。"""
    at = text.index(marker)
    start = text.index("{", at)
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    raise ValueError(f"{marker} 那一格没有闭合")


def catalog(path: Path) -> dict[str, str]:
    """取 `settings.sources.reasons` 那一批键。

    两条解析上的硬要求，都各对应一次真踩过的坑：
    - **括号配对到 reasons 自己那一格**——上一版顺着 `sources` 那块往下扫，把兄弟格
      `title: '信源'` 也当成原因码（10-26 门自己的测试当场抓到）；
    - **键必须行首锚定**——全文正则 `key: value` 会把英文句子里的 `obtained:` 当成一个键。
    """
    reasons = _block_after(_block_after(path.read_text(encoding="utf-8"), "sources: {"), "reasons: {")

    out: dict[str, str] = {}
    for line in reasons.split("\n"):
        m = re.match(r"^\s+([a-z][a-z0-9_]{2,40}): (.*?)\s*,?$", line)
        if m:
            out[m.group(1)] = m.group(2).strip("\"'")
    return out


def check(api_path: Path = API, collectors_dir: Path = COLLECTORS,
          i18n_dir: Path = I18N) -> tuple[list[str], list[str], list[str]]:
    """回 (红, 警告, 死句子)。红＝缺句子 / 空句子 / 句子里点了工件名。"""
    codes = screen_codes(api_path, collectors_dir)
    per_lang = {lang: catalog(i18n_dir / f"{lang}.ts") for lang in LANGS}
    known = set.intersection(*[set(v) for v in per_lang.values()]) if per_lang else set()

    red: list[str] = []
    for code in sorted(codes - known):
        missing = [lang for lang in LANGS if code not in per_lang[lang]]
        red.append(f"{code} —— 目录里没有这一句（缺：{('四语全缺' if len(missing) == len(LANGS) else '、'.join(missing))}）"
                   "；屏幕上这一格会落「暂未适配」，用户看不见下一步")
    for code in sorted(codes & known):
        for lang in LANGS:
            sentence = per_lang[lang][code]
            if not sentence.strip():
                red.append(f"{code}/{lang} —— 句子是空的")
                continue
            for artifact, pattern in ARTIFACTS:
                if pattern.search(sentence):
                    red.append(f"{code}/{lang} —— 那句话点了{artifact}：{sentence[:48]!r}")
                    break

    # 警告：采集器自己的状态词，今天到不了屏幕（写了四语句子也没人看）
    state_words: set[str] = set()
    for py in sorted(collectors_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id.startswith("STATE_") for t in node.targets):
                state_words |= {s for s in _strings(node.value) if _looks_like_code(s)}
    warn = sorted((state_words - codes - known))

    dead = sorted(known - codes - state_words)
    return red, warn, dead


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="机器读的摘要")
    args = ap.parse_args(argv)

    red, warn, dead = check()
    if args.json:
        print(f'{{"red": {len(red)}, "warn": {len(warn)}, "dead": {len(dead)}}}')
    else:
        print(f"屏幕可达状态词：{len(screen_codes())} 个；四语都有的：{len(set.intersection(*[set(catalog(I18N / f'{l}.ts')) for l in LANGS]))} 个")
        if red:
            print(f"\nRED — {len(red)} 处指引断供：")
            for line in red:
                print("  -", line)
            print("\n补法：四语目录 `settings.sources.reasons` 各加一句，且那句话要带下一步（裁定 203）。")
        if warn:
            print(f"\nWARN — {len(warn)} 个采集器自己的状态词，今天到不了屏幕（不判红，省得逼出没人看的死句子）：{', '.join(warn)}")
        if dead:
            print(f"\nWARN — {len(dead)} 句目录里写了但没人产出：{', '.join(dead)}（清了或留着都行，别当成绩）")
        if not red:
            print("\nGREEN — 屏幕上会出现的状态词都有四语句子，且没点工件名")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())

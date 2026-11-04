"""``plobi mind`` — is the official brain actually alive on this machine?

内核命令（裁定 198／10-23「不要再是插件形态」）：原在 plugins/memory/mind/cli.py，靠
「当前 provider 是 mind 才被插件发现」挂上，provider 键一空它就消失。它的回答不需要 provider 在跑
（读的是两个仓与画像文件），所以现在由内核直接注册，与 provider 是谁无关。

The provider mounts itself from ``agent_init``, writes through the serialized
writer, and reads the persona slice into every primary session. None of that is
visible anywhere, which is how "the brain is live" became an article of faith:
the daily digest was landing on disk but never entering git, and nothing said so.

This command answers four questions with commands, not adjectives. Read-only: it
never writes, never commits, never touches the network (``@{u}`` is a local ref).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from plobi.mind.paths import real_target, resolve_roots

#: 摘要落点的前缀由 agenda 那协调器拥有，这里不抄第二份路径规则
# 摘要落点在脑里（用户 10-24：surface 不进记忆系统）；以前这里是 Surface/projects/Plobi/daily。
_DIGEST_PREFIX = "Mind/wiki/daily"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60
    )


def _digest_state(roots) -> tuple[str, str]:
    """今天的摘要：在不在盘上、进没进它所属那个仓的 git。"""
    logical = f"{_DIGEST_PREFIX}/{datetime.now().date().isoformat()}.md"
    path = real_target(logical, roots)
    if not path.is_file():
        return logical, "缺席（今天还没有任何主会话写过它）"
    repo = None
    for candidate in (roots.surface, roots.mind):
        if candidate is None:
            continue
        try:
            inner = path.resolve().relative_to(candidate.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        if _git(candidate, "rev-parse", "--show-toplevel").returncode == 0:
            repo, inner_path = candidate, inner
            break
    if repo is None:
        return logical, f"在盘上（{path}），但落点不在脑自己的仓里 ⇒ 无从记账"
    tracked = _git(repo, "ls-files", "--error-unmatch", inner_path).returncode == 0
    dirty = bool(
        _git(repo, "status", "--porcelain", "--", inner_path).stdout.strip()
    )
    if not tracked:
        return logical, f"在盘上、**未入库**（{repo.name} 里是 untracked）"
    if dirty:
        return logical, f"在盘上、已入库但这一笔有未提交改动（{repo.name}）"
    return logical, f"已入库（{repo.name}）"


def _persona_state(roots) -> str:
    """画像两档：详版在脑里（persona/profile.md），精简版是 L0 的 USER.md。

    surface 不进记忆系统（用户 2026-10-24 明确）：这一行以前读 Surface/meta/profile，
    那份已按用户令删除；详版搬回脑里，摘要那份由 L0 自己维护。
    """
    from plobi.mind.reader import PERSONA_RELATIVE, MindReader
    from plobi_constants import get_plobi_home

    persona = MindReader().persona()
    where = real_target(PERSONA_RELATIVE, roots)
    detail = (
        f"{len(persona)} 字符（脑 · {where}）" if persona else f"空 —— 读不到 {where}"
    )
    user_md = get_plobi_home() / "memory" / "USER.md"
    if user_md.is_file():
        summary = f"{user_md.stat().st_size} 字节（L0 精简版）"
        # 精简版是**手工蒸**出来的（改详版→再蒸馏一次），没有任何自动派生路径，
        # 所以它会悄悄落后。把"落后"这件事变成看得见的一句话，而不是靠谁记得。
        try:
            drift = user_md.stat().st_mtime - (where.stat().st_mtime if where.is_file() else 0)
            if where.is_file() and drift < 0:
                summary += f" · 落后详版 {int(-drift / 3600) // 24 + 1} 天，该重蒸一次"
        except OSError:
            pass
    else:
        summary = "还没生成（L0 精简版）"
    return f"详版 {detail} · 精简版 {summary}"


def _repo_state(repo: Optional[Path]) -> str:
    if repo is None:
        return "未配置"
    if not repo.is_dir():
        return f"{repo} 不存在"
    if _git(repo, "rev-parse", "--show-toplevel").returncode != 0:
        return f"{repo} 不是 git 仓"
    counts = _git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD")
    if counts.returncode == 0:
        behind, ahead = (part.strip() for part in counts.stdout.split("\t"))
        movement = f"behind={behind} ahead={ahead}"
    else:
        movement = "没有 upstream（@{u} 解不出来）"
    dirty = len(
        [
            line
            for line in _git(repo, "status", "--porcelain").stdout.splitlines()
            if line.strip()
        ]
    )
    return f"{repo} · {movement} · 未提交 {dirty} 档"


def status_lines() -> list[tuple[str, str]]:
    """The four readouts, in the order a person should be able to read them."""
    from agent.memory_manager import resolve_memory_provider_name

    try:
        from plobi_cli.config import load_config

        configured = load_config().get("memory", {}).get("provider")
    except Exception:  # a config we cannot read is itself the finding
        configured = None
    name = resolve_memory_provider_name(configured)
    roots = resolve_roots(None)

    availability = "无法解析 MIND_ROOT（脑不在这台的配置里）"
    provider_line = f"{name}（配置里写的是 {configured!r}）"
    if roots is not None:
        try:
            from plugins.memory import load_memory_provider

            provider = load_memory_provider(name)
            availability = (
                "可用" if provider is not None and provider.is_available() else "不可用"
            )
        except Exception as exc:  # pragma: no cover - defensive readout
            availability = f"加载失败：{exc}"

    digest_logical, digest_state = _digest_state(roots) if roots else ("—", "无根可解析")
    return [
        ("provider", provider_line + f" · is_available: {availability}"),
        ("画像", _persona_state(roots) if roots else "无根可解析"),
        (f"今日摘要 {digest_logical}", digest_state),
        ("脑仓 MIND_ROOT", _repo_state(roots.mind) if roots else "未配置"),
        ("人读面 SURFACE_ROOT", _repo_state(roots.surface) if roots else "未钉（人读面还在脑仓里）"),
    ]


def register_cli(subparser: argparse.ArgumentParser) -> None:
    """Build the ``plobi mind`` subcommand tree (called during argparse setup)."""
    subs = subparser.add_subparsers(dest="mind_command")
    status_parser = subs.add_parser(
        "status",
        help="官方脑在这台活着没有：画像、今日摘要、两个脑仓的记账状态",
        description=(
            "Read-only proof that the official memory brain is live: what the agent "
            "reads as your persona, whether today's digest landed and got committed "
            "into the repo that owns it, and each brain repo's ahead/behind plus "
            "uncommitted files. Never writes, never pushes, never hits the network."
        ),
    )
    status_parser.add_argument("--json", action="store_true", help="机器可读输出")
    subparser.set_defaults(handler=_dispatch)


def _dispatch(args: argparse.Namespace) -> int:
    lines = status_lines()
    if getattr(args, "json", False):
        print(json.dumps({k: v for k, v in lines}, ensure_ascii=False, indent=2))
        return 0
    width = max(len(k) for k, _ in lines)
    for key, value in lines:
        print(f"{key.ljust(width)}  {value}")
    return 0


def mind_command(args: argparse.Namespace) -> int:
    """Entry point the memory-plugin CLI discovery wires to ``plobi mind``."""
    handler = getattr(args, "handler", None)
    if callable(handler):
        return int(handler(args))
    return _dispatch(args)

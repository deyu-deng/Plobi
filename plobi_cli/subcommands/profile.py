"""``plobi agent`` subcommand parser.

Extracted verbatim from ``plobi_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_profile_parser(subparsers, *, cmd_profile: Callable) -> None:
    """Attach the ``agent`` subcommand to ``subparsers`` (old name still parses).

    名字反过来了（11-05 随 `WP-PROFILE-CANCEL`）：**主名是 `plobi agent`**，
    ``plobi profile`` 只作为手打惯了的旧写法收着——它是别名，不是命令族的名字。
    派发靠 ``set_defaults(func=…)``，所以两个名字走的是同一个处理器，功能不缩。

    第六轮（11-05）把 ``help=`` 那几行改口：这一屏是用户和外部 Agent 唯一读得到的
    说明书。dest 仍叫 ``profile_name``（Python 标识符，取值在 main.py），屏上印出来
    那一个由 ``metavar="AGENT"`` 接管。判据在
    ``tests/plobi_cli/test_cli_help_says_agent_not_profile.py``。
    """
    # ``plobi agent`` 是同一个命令族的名字（用户 10-27：Agent 就是名册上的一行，
    # 「profile」这个词不该再教给 Agent；11-05：也别再摆在菜单第一位）。
    profile_parser = subparsers.add_parser(
        "agent",
        aliases=["profile"],
        help="Manage agents — the roster of identities",
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action")

    profile_subparsers.add_parser("list", help="List all agents (the roster)")
    profile_use = profile_subparsers.add_parser(
        "use", help="Remember which agent new chats use"
    )
    profile_use.add_argument(
        "profile_name", metavar="AGENT", help="Agent name (or 'default')"
    )

    # create / import / install 三处入口在裁 178 乙案第 4 步关掉（main.py 里那句
    # sys.exit(2) 与去向），parser 留着：手打惯的人敲到它，得到的是「去哪儿加 Agent」，
    # 不是 argparse 那句 invalid choice。
    profile_create = profile_subparsers.add_parser(
        "create", help="Closed — agents come from the roster (`plobi agent list`)"
    )
    profile_create.add_argument(
        "profile_name",
        metavar="AGENT",
        help="Agent name (lowercase, alphanumeric)",
    )
    profile_create.add_argument(
        "--clone",
        action="store_true",
        help="Copy config.yaml, .env, the identity file, and skills from the current agent",
    )
    profile_create.add_argument(
        "--clone-all",
        action="store_true",
        help="Full copy of the current agent (all state, excluding its own history)",
    )
    profile_create.add_argument(
        "--clone-from",
        metavar="SOURCE",
        help="Agent to copy from; implies --clone unless --clone-all is set",
    )
    profile_create.add_argument(
        "--no-alias", action="store_true", help="Skip wrapper script creation"
    )
    profile_create.add_argument(
        "--no-skills",
        action="store_true",
        help="Start with no bundled skills (opts out of `plobi update` skill sync)",
    )
    profile_create.add_argument(
        "--description",
        default=None,
        help="One- or two-sentence description of what this agent is good at. "
             "Used by the kanban decomposer to route tasks based on role instead "
             "of name alone. Skip and add later via `plobi agent describe`.",
    )

    profile_delete = profile_subparsers.add_parser("delete", help="Delete an agent")
    profile_delete.add_argument("profile_name", metavar="AGENT", help="Agent to delete")
    profile_delete.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompt"
    )

    # 裁定 42 §42.4：分身的家不再按项目复制之后，存量目录只标失效、绝不删除——
    # 这一格就是那个「点头」动作本身（清理目录仍是另一件要单独确认的事）。
    # 家退休之后，那枚章落在名册行的 ``archived`` 那一格（main.py 的 retire 分支）。
    profile_retire = profile_subparsers.add_parser(
        "retire",
        help="Archive an agent (the row stays in the roster, hidden from the list)",
    )
    profile_retire.add_argument(
        "profile_name", metavar="AGENT", help="Agent to archive"
    )
    profile_retire.add_argument(
        "--reason",
        default="",
        help="Why this agent is archived; recorded on the roster row",
    )

    profile_describe = profile_subparsers.add_parser(
        "describe",
        help="Read or set an agent's description (used by the kanban orchestrator)",
    )
    profile_describe.add_argument(
        "profile_name",
        nargs="?",
        metavar="AGENT",
        default=None,
        help="Agent to describe (omit + use --all --auto to sweep)",
    )
    profile_describe.add_argument(
        "--text",
        default=None,
        help="Set description to this exact text (overwrites any existing description)",
    )
    profile_describe.add_argument(
        "--auto",
        action="store_true",
        help="Auto-generate the description with the auxiliary model",
    )
    profile_describe.add_argument(
        "--overwrite",
        action="store_true",
        help="With --auto, replace user-authored descriptions too (default: only "
             "fill in missing or previously-auto descriptions)",
    )
    profile_describe.add_argument(
        "--all",
        dest="all_missing",
        action="store_true",
        help="With --auto, run on every agent missing a description",
    )

    profile_show = profile_subparsers.add_parser("show", help="Show one agent")
    profile_show.add_argument("profile_name", metavar="AGENT", help="Agent to show")

    profile_alias = profile_subparsers.add_parser(
        "alias", help="Manage wrapper scripts"
    )
    profile_alias.add_argument("profile_name", metavar="AGENT", help="Agent name")
    profile_alias.add_argument(
        "--remove", action="store_true", help="Remove the wrapper script"
    )
    profile_alias.add_argument(
        "--name",
        dest="alias_name",
        metavar="NAME",
        help="Custom alias name (default: the agent name)",
    )

    profile_rename = profile_subparsers.add_parser("rename", help="Rename an agent")
    profile_rename.add_argument("old_name", metavar="AGENT", help="Current name")
    profile_rename.add_argument("new_name", metavar="NEW_NAME", help="New name")

    profile_export = profile_subparsers.add_parser(
        "export", help="Export an agent's files to an archive"
    )
    profile_export.add_argument(
        "profile_name", metavar="AGENT", help="Agent to export"
    )
    profile_export.add_argument(
        "-o", "--output", default=None, help="Output file (default: <name>.tar.gz)"
    )

    profile_import = profile_subparsers.add_parser(
        "import", help="Closed — agents come from the roster (`plobi agent list`)"
    )
    profile_import.add_argument("archive", help="Path to .tar.gz archive")
    profile_import.add_argument(
        "--name",
        dest="import_name",
        metavar="NAME",
        help="Agent name (default: inferred from archive)",
    )

    # ---------- Distribution subcommands (issue #20456) ----------
    profile_install = profile_subparsers.add_parser(
        "install",
        help="Closed — agents come from the roster (`plobi agent list`)",
        description=(
            "Install a Plobi agent distribution. SOURCE can be a git URL "
            "(github.com/user/repo, https://..., git@...) or a local "
            "directory containing distribution.yaml at its root."
        ),
    )
    profile_install.add_argument(
        "source",
        help="Distribution source (git URL or local directory)",
    )
    profile_install.add_argument(
        "--name", dest="install_name", metavar="NAME",
        help="Override the agent name (default: read from manifest)",
    )
    profile_install.add_argument(
        "--alias", action="store_true",
        help="Create a shell wrapper alias for the installed agent",
    )
    profile_install.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing agent of the same name (user data preserved)",
    )
    profile_install.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip manifest preview confirmation",
    )

    profile_update = profile_subparsers.add_parser(
        "update",
        help="Re-pull a distribution and apply updates (user data preserved)",
        description=(
            "Fetch the distribution from its recorded source and overwrite "
            "distribution-owned files (SOUL.md, skills/, cron/, mcp.json). "
            "User data (memories, sessions, auth, .env) is never touched. "
            "config.yaml is preserved unless --force-config is passed."
        ),
    )
    profile_update.add_argument(
        "profile_name", metavar="AGENT", help="Agent to update"
    )
    profile_update.add_argument(
        "--force-config", action="store_true",
        help="Also overwrite config.yaml (normally preserved to keep user overrides)",
    )
    profile_update.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation",
    )

    profile_info = profile_subparsers.add_parser(
        "info",
        help="Show an agent's distribution manifest (version, requirements, source)",
    )
    profile_info.add_argument(
        "profile_name", metavar="AGENT", help="Agent to inspect"
    )

    profile_parser.set_defaults(func=cmd_profile)

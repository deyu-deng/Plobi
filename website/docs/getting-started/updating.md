---
sidebar_position: 3
title: "Updating & Uninstalling"
description: "There is no automatic update in this build — here is how to actually move versions, and how to uninstall"
---

# Updating & Uninstalling

## Read this first: `plobi update` does not update anything

This build has **no self-update**. `plobi update` is still registered as a
subcommand (other code references the command name), but its body prints one
notice and exits successfully:

```
Plobi 本地开发版：git 自更新已禁用（历史事故防护）。代码同步由负责 Agent 手动进行。
```

Every flag you may see documented elsewhere — `--branch`, `--check`, `--backup`,
`--force`, `--all` — is accepted by the argument parser and **never consumed**.
Nothing is fetched, nothing is reinstalled, and no rollback machinery runs.

Two reasons, both deliberate:

1. **Incident protection.** Two past incidents (2026-09-06 and 2026-09-07) had the
   runtime git path destroy a working tree / `.git` during a routine "update". The
   whole class of runtime git-mutating paths was frozen rather than patched.
2. **This is a fork that does not track upstream.** Code sync is a human/Agent
   decision made per release, never an automatic pull.

:::danger
Do not treat `plobi update` as your security-patch path. A command that exits `0`
while changing nothing is worse than a failing command — it looks like it worked.
Watch [Releases](https://github.com/deyu-deng/Plobi/releases) and update
explicitly with the steps below.
:::

## How to actually move to a new version

Pick the row matching how you installed. None of them touch your data directory.

| 安装方式 | 更新动作 | 你的数据 |
| --- | --- | --- |
| **Desktop installer** (`.dmg` / `.exe`) | Download the newer installer from [Releases](https://github.com/deyu-deng/Plobi/releases) and install over the top. | Untouched — it lives in `~/.plobi` (`%APPDATA%\Plobi` on Windows). |
| **Source install** (`git` + `uv`/venv) | The four steps below. | Untouched. |
| **Docker / Homebrew** | **Not published yet.** See [Release channels](#release-channels-are-not-published-yet). | n/a |

### Source install, step by step

```bash
cd /path/to/Plobi                 # the checkout you made with git clone

# 1. Back up first — config, keys, sessions, skills all live outside the repo.
cp -a ~/.plobi ~/plobi-backup-$(date +%F)

# 2. Move the working tree. The clone's remote is named `github`, not `origin`.
git fetch github && git checkout main && git reset --hard github/main

# 3. Re-resolve dependencies for the extras you installed.
uv sync --extra dev --extra acp --extra messaging --extra web --extra anthropic

# 4. Self-check. Red rows here are what actually broke between versions.
python scripts/plobi/doctor.py
```

`plobi config check` will list config keys your file is missing after a bump, and
`plobi config migrate` walks you through adding them interactively. Neither runs
by itself any more — that prompt used to be part of `plobi update`.

### Restart what was running

```bash
plobi gateway restart     # messaging gateway
# The desktop app: quit from the tray, then relaunch.
```

## Release channels are not published yet

Two channels exist in the tree but have **never shipped a Plobi artifact**, so no
install command through them will work today. They are kept on purpose while
packaging is still moving; this section is the honest status, not an invitation.

| 渠道 | 现状 | 别照抄的东西 |
| --- | --- | --- |
| Docker image | 我方镜像**未发布**。仓库里的 `docker-compose.yml` 已指向 `plobi-agent`，但 `docker pull` 拿不到它。 | 任何 `docker pull …/hermes-agent` 形式的命令——那是别人发布的镜像，不是本项目的构建物。见 [Docker](../user-guide/docker.md)。 |
| Homebrew formula | `packaging/homebrew/plobi-agent.rb` 是**占位**：它的 `url:` 仍指向别人的源码包。 | `brew install plobi-agent`。发布流程本身也还没跑过，见 `packaging/homebrew/README.md` 顶部说明。 |

## Rolling back

```bash
cd /path/to/Plobi
git log --oneline -10
git checkout <commit-hash>        # or a release tag from `git tag --sort=-version:refname`
uv sync --extra dev
plobi doctor
```

If the older version predates config keys you now have, `plobi config check` will
name the unrecognized ones; remove them from `config.yaml` if the old build errors
out. Your `~/.plobi` backup above is the fast way out.

:::note Nix
Nix is no longer an explicitly supported install path (best-effort only) — see
[Nix Setup](./nix-setup.md). If you installed via Nix flake, upgrades and rollback
go through `nix flake update plobi-agent` / `nix profile upgrade plobi-agent` /
`nix profile rollback`.
:::

---

## Uninstalling

```bash
plobi uninstall
```

The uninstaller gives you the option to keep your configuration files (`~/.plobi/`) for a future reinstall.

### Manual Uninstall

```bash
rm -f ~/.local/bin/plobi
rm -rf /path/to/Plobi
rm -rf ~/.plobi            # Optional — keep if you plan to reinstall
```

:::info
If you installed the gateway as a system service, stop and disable it first:
```bash
plobi gateway stop
# Linux: systemctl --user disable plobi-gateway
# macOS: launchctl remove ai.plobi.gateway
```
:::

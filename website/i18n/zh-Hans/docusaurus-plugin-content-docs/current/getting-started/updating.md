---
sidebar_position: 3
title: "更新与卸载"
description: "本构建没有自动更新 —— 这里写清真正可用的升级方式，以及卸载"
---

# 更新与卸载

## 先读这条：`plobi update` 什么都不会更新

本构建**没有自更新**。`plobi update` 仍然注册为子命令（别处代码引用这个命令名），
但它的函数体只打印一行提示然后正常退出：

```
Plobi 本地开发版：git 自更新已禁用（历史事故防护）。代码同步由负责 Agent 手动进行。
```

你在别处看到的旗标 —— `--branch`、`--check`、`--backup`、`--force`、`--all` ——
参数解析器照收，但**从不被读取**。不拉代码、不重装依赖、也不会跑任何回滚机制。

两个原因，都是有意的：

1. **事故防护。** 2026-09-06 与 2026-09-07 两次事故里，运行期 git 路径在一次普通
   "更新"中摧毁过工作树 / `.git`。所以整类"运行期改 git 状态"的路径是被**冻结**，不是被修补。
2. **本 fork 不再同步上游。** 代码同步是每次发布时人 / Agent 的显式决定，不会自动 pull。

:::danger
不要把 `plobi update` 当成你的安全补丁通道。一条什么都不改却返回 0 的命令，
比一条报错的命令更糟 —— 它看起来像是成功了。请关注
[Releases](https://github.com/deyu-deng/Plobi/releases)，用下面的步骤显式更新。
:::

## 真正可用的升级方式

按你的安装方式选一行。任何一种都**不会碰**你的数据目录。

| 安装方式 | 更新动作 | 你的数据 |
| --- | --- | --- |
| **桌面安装包**（`.dmg` / `.exe`） | 从 [Releases](https://github.com/deyu-deng/Plobi/releases) 下载新版安装包覆盖安装。 | 不动 —— 数据在 `~/.plobi`（Windows 是 `%APPDATA%\Plobi`）。 |
| **源码安装**（`git` + `uv`/venv） | 见下面四步。 | 不动。 |
| **Docker / Homebrew** | **尚未发布**。见下方「发布渠道现状」。 | 不适用 |

### 源码安装四步

```bash
cd /path/to/Plobi                 # 你 git clone 出来的那份

# 1. 先备份 —— 配置、密钥、会话、技能都在仓库之外。
cp -a ~/.plobi ~/plobi-backup-$(date +%F)

# 2. 移动工作树。本仓远端名字叫 `github`，不是 `origin`。
git fetch github && git checkout main && git reset --hard github/main

# 3. 按你装过的 extras 重新解析依赖。
uv sync --extra dev --extra acp --extra messaging --extra web --extra anthropic

# 4. 自检。这里的红项才是版本之间真的坏掉的东西。
python scripts/plobi/doctor.py
```

升级后 `plobi config check` 会列出你的配置文件缺了哪些新键，
`plobi config migrate` 带你逐项补齐。这两件事**都不再自动跑** —— 以前它们是
`plobi update` 流程的一部分。

### 把在跑的东西重启

```bash
plobi gateway restart     # 消息网关
# 桌面端：从托盘退出，再重新打开。
```

## 发布渠道现状（还没发布）

仓库里有两条渠道，但它们**从未发布过任何一个 Plobi 构建物**，所以今天照命令敲是拿不到东西的。
打包还在推进中，这两条渠道是有意保留的；本节给的是实话，不是邀请。

| 渠道 | 现状 | 别照抄 |
| --- | --- | --- |
| Docker 镜像 | 我方镜像**未发布**。仓内 `docker-compose.yml` 已指向 `plobi-agent`，但 `docker pull` 拉不到它。 | 任何 `docker pull …/hermes-agent` 形式的命令 —— 那是别人发布的镜像，不是本项目的构建物。见 [Docker](../user-guide/docker.md)。 |
| Homebrew formula | `packaging/homebrew/plobi-agent.rb` 是**占位**：它的 `url:` 仍指向别人的源码包。 | `brew install plobi-agent`。发布流程本身也还没跑过，见 `packaging/homebrew/README.md` 顶部说明。 |

## 回滚

```bash
cd /path/to/Plobi
git log --oneline -10
git checkout <commit-hash>        # 或从 `git tag --sort=-version:refname` 里挑一个发布标签
uv sync --extra dev
plobi doctor
```

如果回到的版本还没有你现在的某些配置键，`plobi config check` 会点出不认识的项；
旧构建报错时把它们从 `config.yaml` 里删掉即可。上面那份 `~/.plobi` 备份是最快的退路。

:::note Nix
Nix 不再是显式支持的安装路径（尽力而为）—— 见 [Nix Setup](./nix-setup.md)。
用 Nix flake 安装的话，升级与回滚走
`nix flake update plobi-agent` / `nix profile upgrade plobi-agent` / `nix profile rollback`。
:::

---

## 卸载

```bash
plobi uninstall
```

卸载程序会让你选择是否保留配置文件目录（`~/.plobi/`），以便将来重装。

### 手动卸载

```bash
rm -f ~/.local/bin/plobi
rm -rf /path/to/Plobi
rm -rf ~/.plobi            # 可选 —— 打算重装就留着
```

:::info
如果你把网关装成了系统服务，先停掉并禁用：
```bash
plobi gateway stop
# Linux: systemctl --user disable plobi-gateway
# macOS: launchctl remove ai.plobi.gateway
```
:::

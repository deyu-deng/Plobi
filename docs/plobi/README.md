# Plobi — 文档指针（本目录已清空）

**Plobi 的项目级文档真源不在这里。** 全部平铺在仓库外的
`D:\Projects\Plobi\Docs\`，索引 = [`Docs/README.md`](../../../Docs/README.md)，
纪律 = 其 §0，入口链 = `Docs/HANDOVER.md` → `Docs/MASTER-PLAN.md` → `Docs/REQUIREMENTS.md`。

本目录（`Code/docs/plobi/`）曾在 2026-09 按 Diátaxis 建过 `adr/ specs/ runbooks/ reference/
audit/ templates/ plans/ archive/ north_star/` 树，内容已整体迁到上面的 `Docs/`，树本身随
`.git` 被 `plobi update` 摧毁的事故清空——**现在只剩本文件与 `profiles/` 两份 yaml**。
2026-09-19 已按新纪律把 `Docs/` 从 238 份砍到 100 份，其中就包括曾挂在这条链上的旧 Plobi 叙事
（`plobi-BULEPRINT.md`、`plobi-CONTEXT.md`、`plobi-AUDIT.md`、`plobi-README.md`、
`UI_DESIGN_SPEC.md` ×2、`INDEX.md`、`SLICES.md`）与失真的 `IMPLEMENTED_FEATURES.md`
（2026-07-13 快照，早已不反映代码）。

## 要找的东西在哪

| 你要的 | 现在在哪 |
|---|---|
| 最终形态契约（三层 Agent / 额度派工 / HID / 人机面） | `Docs/GRILL_FREEZE.md` |
| MVP 与 M1 范围、§8.2 派工骨架 | `Docs/MVP-AI-Secretary-Requirements.md` |
| 排期、每刀 WP、决策点 | `Docs/MASTER-PLAN.md` |
| 需求登记（用户原话 → 判定 → 去向） | `Docs/REQUIREMENTS.md` |
| 架构裁定（现至裁定 41；**裁定 2：禁 `POST /api/chat`，聊天走 gateway RPC**） | `Docs/ARCH-RULINGS_2026-09-08.md` |
| UI 冻结规格（三栏、§3.6 单一 Shell + 单一 ChatSurface、状态机、API 契约） | `Docs/ui-l1-console-spec.md` + `Docs/ARCH-UI-MASTER.md` |
| ADR（含 ADR-0010 采集黑名单/隐私边界、0011 三层 Agent） | `Docs/00xx-*.md` |
| 代码实际实现了什么 | **读代码**（不再有逐模块清单；旧清单已因失真删除） |
| Master profile 模板 | `profiles/master/config.yaml` 文件还在，但**裁定 6：不创建 `master` profile**——勿据此启用，L1 留在当前 profile |

实现级细节（模块说明、运行手册）若确实要随代码走，可以新增在本目录，并在 `Docs/README.md` 挂一行；
否则一律写到 `Docs/`。

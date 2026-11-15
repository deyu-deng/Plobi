/**
 * 名手（L3 专家）的**分类键与顺序**，前端只此一处（2026-11-15 收口）。
 *
 * 真源在后端 `plobi/delegation/expert_roles.py` 的 `TRADES`。这里是一份**镜像**：
 * TypeScript 没法在编译期读 Python 常量，所以键集合与顺序由
 * `apps/desktop/src/shared/expert-trades.test.ts` 对表锁死——后端加一类而这里没跟上，
 * 测试红；这里自己编一类后端不认的，测试也红。
 *
 * 为什么单开这个文件：`store/` 不许反向依赖 UI 模块（见 `store/conversation-view.ts`），
 * 而侧栏名册与 store 的类型必须同一套。放在 `shared/` 两边都引用它，不制造第二个真源。
 */
export const EXPERT_TRADES = ['file', 'browser', 'software', 'computer'] as const

/** 分类键的联合类型，从数组派生——别手写第二份字面量。 */
export type ExpertRoleKey = (typeof EXPERT_TRADES)[number]

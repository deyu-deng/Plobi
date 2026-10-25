/**
 * 设置 → 信源那几段的**折叠状态**（用户 10-25：「**我希望不同大类的信源也可以折叠起来**」）。
 *
 * 这里存的不是"采集事实"，是**显示层的临时形状**，所以它走桌面端自己的持久化那道闸
 * （`lib/storage.ts` + `lib/persisted.ts`），**不进 `sources.yaml`**——那一格是他拍的
 * "本机记住"，而且配置层一旦被界面偏好污染，"这条信源为什么没采"就有了两处可能的答案。
 *
 * 三件事决定这一档的形状：
 *
 * 1. **键认类别，不认信源 id**。类别是闭集（`registry.SOURCE_CATEGORIES`，七个值），段数不会涨；
 *    信源会增减，拿 id 当键就等于"删掉一条信源"在偏好表里永远留一格垃圾。
 * 2. **三态，不是开关**：展开／收起／**没动过**。只有"没动过"那段吃自动规则（有报错 ⇒ 展开）。
 *    他明说过的段永不被读数翻面——否则一条信源从报错回到正常，会把他刚折好的那段自己弹开；
 *    那是**屏幕在动**，不是数据在动。
 * 3. **只存明说过的**。没点过的类别在表里根本没有键，所以"清掉这一个键"＝回到自动规则，
 *    而不是留下一堆 `open` 的残留。
 */

import { type Codec, persistentAtom } from '@/lib/persisted'
import { storageKey } from '@/lib/storage'

export type Fold = 'collapsed' | 'open'

const FOLDS: readonly Fold[] = ['collapsed', 'open']

/**
 * 「装一个自己的」那一段里**是入口不是数据**：按自动规则它 0 行 ⇒ 会被折起来，
 * 那颗安装入口第一天就被折没了。所以它单独吃一条恒展开。
 */
const ACTION_FIRST: readonly string[] = ['custom']

/** 存坏了、或别的写方塞了别的词 ⇒ 当作没记过（回到读数决定），不猜一个出来。 */
function sanitize(raw: unknown): Record<string, Fold> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {}
  }

  const kept: Record<string, Fold> = {}

  for (const [category, fold] of Object.entries(raw)) {
    if (FOLDS.includes(fold as Fold)) {
      kept[category] = fold as Fold
    }
  }

  return kept
}

/** 空表不占一格：编成 `null` 会让 storage 那道闸把键**删掉**（与 `Codecs.stringArray` 同款）。 */
const folds: Codec<Record<string, Fold>> = {
  decode: raw => sanitize(JSON.parse(raw) as unknown),
  encode: value => (Object.keys(value).length === 0 ? null : JSON.stringify(value))
}

export const $fold = persistentAtom<Record<string, Fold>>(
  storageKey('desktop.settings.sources.fold'),
  {},
  folds
)

/**
 * 这一段现在画不画行。顺序是判据，别调换：
 * **他明说过的** > **那段里是入口** > **有没有报错**。
 */
export function isOpen(fold: Record<string, Fold>, group: { category: string, errors: number }): boolean {
  const decided = fold[group.category]

  if (decided) {
    return decided === 'open'
  }

  if (ACTION_FIRST.includes(group.category)) {
    return true
  }

  return group.errors > 0
}

/** 记下他这一次点的结果（写进表里＝从此这一段不再吃读数）。 */
export function decide(category: string, open: boolean) {
  $fold.set({ ...$fold.get(), [category]: open ? 'open' : 'collapsed' })
}

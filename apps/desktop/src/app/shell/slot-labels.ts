/**
 * slot 声明里的 `labelKey` 怎么变成屏上的字。
 *
 * **slot 声明是数据**（插件也能写），它不该知道屏上是哪一棵 i18n 树 ⇒
 * 「键 → 人话」这一步由**宿主**做。
 *
 * 两条路都认：
 *
 * - `label`：直接给人话（插件写的那几格走这条——它没有宿主 i18n 的键）；
 * - `labelKey`：**点号路径**（`surface.agentDossier`／`workbench.tabs.files`），
 *   对着宿主那棵 i18n 树走。
 *
 * 为什么是点号路径而不是「最后一段」：同一个键名在不同分组里都有（`files` 既是
 * `workbench.tabs.files` 也是 `workbench.actions.files`）——只取最后一段会把两处
 * 认成同一句。路径是**唯一**的。
 */
export function resolveSlotLabel(t: unknown, key: string | undefined): string | undefined {
  if (!key) {
    return undefined
  }

  let node: unknown = t

  for (const part of key.split('.')) {
    if (!node || typeof node !== 'object') {
      return undefined
    }
    node = (node as Record<string, unknown>)[part]
  }

  return typeof node === 'string' && node.length > 0 ? node : undefined
}

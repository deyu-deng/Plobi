/**
 * 桌面日志的排版与去噪（纯函数，不 import electron：node:test 直接跑）。
 *
 * 2026-11-18 装机现场：日志里最吵的两样东西都不是「发生了什么」，而是
 * 「同一句话说两遍」与「机器在跟自个儿说话」。这一模块把这两样收掉：
 *
 *  1. **同一句不说两遍**——诊断行的标签与说明相同时只写一遍
 *     （旧形状是 `暂时无法获取出口状态 :: 暂时无法获取出口状态`）。
 *  2. **状态没变就不落笔**——轮询型诊断同一句话每轮都写一遍，
 *     读的人只会被刷屏（额度目录那一族一次轮询刷四行就是这么来的）。
 *
 * 屏上只写「这是什么」：日志不是内部推理的记录本。
 */

/** 诊断行：标签 + 可选的补充说明。 */
export type DiagnosticRow = { label: string; toolTip?: string }

/**
 * 把诊断行排成一行日志。
 *
 * 标签与说明**相同**时只写标签（`a :: a` 是同一句话说两遍）；
 * 不同时才写成 `标签 :: 说明`。空说明只留标签。
 */
export function formatDiagnosticRows(rows: DiagnosticRow[]): string {
  return (rows || [])
    .map(row => {
      const label = String(row?.label ?? '').trim()
      const tip = String(row?.toolTip ?? '').trim()

      if (!tip || tip === label) {
        return label
      }

      return `${label} :: ${tip}`
    })
    .filter(Boolean)
    .join(' | ')
}

/**
 * 「状态变了才落笔」的落笔器。
 *
 * 每把钥匙记着上一句写过的话；同一句再来一次就不写（轮询型诊断的救命药）。
 * `clear(key)` 把那把钥匙擦掉——状态好了以后再坏，下一次仍要出声。
 */
export function createChangeOnlyLogger(sink: (line: string) => void) {
  const last = new Map<string, string>()

  return {
    /** 落一笔；返回 true 表示这一句真写出去了（false＝与上一句相同，被吞掉）。 */
    emit(key: string, line: string): boolean {
      if (last.get(key) === line) {
        return false
      }

      last.set(key, line)
      sink(line)

      return true
    },
    /** 擦掉那把钥匙：下一次 emit 一定出声。 */
    clear(key: string): void {
      last.delete(key)
    }
  }
}

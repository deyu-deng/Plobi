/**
 * 设置里那一页**接得上去**吗：它是挂在 `memory.provider` 字段的 `descriptionExtra` 上的。
 * 这条守两件容易静默失效的事——provider 不在 Memory 段的字段表里（页永远不渲染），
 * 或者挂载那一行被谁删了（页还在、但没入口）。`MemoryLedgers` 自己的测试只证组件能画。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = join(process.cwd(), 'src', 'app', 'settings')

describe('settings memory page wiring', () => {
  it('memory.provider 在 Memory & Context 段的字段表里', () => {
    const constants = readFileSync(join(SRC, 'constants.ts'), 'utf8')
    // 取 Memory & Context 那一段（从它的 label 到下一段的起点），断言 provider 在字段表里。
    const start = constants.indexOf("label: 'Memory & Context'")
    expect(start).toBeGreaterThan(0)
    const memorySection = constants.slice(start, constants.indexOf("label: 'Skills'", start))
    expect(memorySection).toContain("'memory.provider'")
  })

  it('config-settings 把它挂在 memory.provider 那一行的 descriptionExtra 上', () => {
    const source = readFileSync(join(SRC, 'config-settings.tsx'), 'utf8')
    expect(source).toMatch(/key === 'memory\.provider'/)
    expect(source).toContain('<MemoryLedgers />')
  })
})

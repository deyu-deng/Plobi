/**
 * 设置里那一页**接得上去**吗：它是挂在 `memory.provider` 字段的 `descriptionExtra` 上的。
 * 这条守两件容易静默失效的事——provider 不在 Memory 段的字段表里（页永远不渲染），
 * 或者挂载那一行被谁删了（页还在、但没入口）。`MemoryLedgers` 自己的测试只证组件能画。
 *
 * **11-26 更新（用户 11-25 23:3x）**：设置屏那几段从 **slot 声明表**派生
 * （设置屏＝第四个位置，一段＝一组配置字段）⇒ 那一段的字段清单**在声明表里**，
 * 不在 `settings/constants.ts`。
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = join(process.cwd(), 'src', 'app', 'settings')
const REGISTRY = join(process.cwd(), 'src', 'app', 'shell', 'slot-registry.ts')

describe('settings memory page wiring', () => {
  it('memory.provider 在 Memory & Context 段的字段表里', () => {
    const registry = readFileSync(REGISTRY, 'utf8')
    // 取 Memory & Context 那一段（从它的声明到下一段的起点），断言 provider 在字段表里。
    const start = registry.indexOf("id: 'memory',")
    expect(start).toBeGreaterThan(0)
    const memorySection = registry.slice(start, registry.indexOf("id: 'voice',", start))
    // 这一段的字段清单与**分组**都在声明里（分组是宿主的形状契约）。
    expect(memorySection).toContain("'memory.provider'")
    expect(memorySection).toContain('settings.groups.memory.external')
  })

  it('config-settings 把它挂在 memory.provider 那一行的 descriptionExtra 上', () => {
    const source = readFileSync(join(SRC, 'config-settings.tsx'), 'utf8')
    expect(source).toMatch(/key === 'memory\.provider'/)
    expect(source).toContain('<MemoryLedgers />')
  })
})

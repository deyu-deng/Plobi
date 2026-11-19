import { beforeEach, describe, expect, it } from 'vitest'

import {
  $monitorCollapsedSections,
  $monitorFloatPosition,
  isMonitorSectionCollapsed,
  setMonitorFloatPosition,
  toggleMonitorSection
} from './layout'

/**
 * 那一张卡的**逐段折叠**与**浮层位置**（Qoder 那一层，11-18 10:0x 拍板 B）。
 *
 * Qoder 的任务监控面板有 13 个分区，每个都能单独收起，状态存在
 * `qoder:workbench:task-monitor-section-visibility`；浮层可拖动并记住位置。
 * 我们照抄这两件。
 *
 * **11-19 17:0x**：这两件从「档案卡的」变成「**这一族那张卡**的」⇒ 名字跟着改
 * （`dossier*` → `monitor*`），**行为一个字没动**、存储键也没换（那个键装的是
 * 折叠状态与位置，不是名字；换键＝把用户已经摆好的作废）。
 *
 * 另起一档而不是并进 `layout.test.ts`：那一档归 `WP-DOSSIER-MONITOR` 认领，
 * 同改一档会被登记册判成「跨线相咬」（裁定 77 的认领范围门也会拦下越界提交）。
 */
describe('那一张卡的逐段折叠与浮层位置', () => {
  beforeEach(() => {
    $monitorCollapsedSections.set({})
    $monitorFloatPosition.set(null)
  })

  it('折起一段就记着，别段不动', () => {
    toggleMonitorSection('stages')

    expect(isMonitorSectionCollapsed('stages')).toBe(true)
    expect(isMonitorSectionCollapsed('progress')).toBe(false)
  })

  it('再点一次就展开', () => {
    toggleMonitorSection('stages')
    toggleMonitorSection('stages')

    expect(isMonitorSectionCollapsed('stages')).toBe(false)
  })

  it('全部展开时不留一份全 false 的垃圾记录', () => {
    toggleMonitorSection('stages')
    toggleMonitorSection('stages')

    expect($monitorCollapsedSections.get()).toEqual({})
  })

  it('浮层位置能存能取（拖完记住）', () => {
    setMonitorFloatPosition({ x: 120, y: 80 })
    expect($monitorFloatPosition.get()).toEqual({ x: 120, y: 80 })

    setMonitorFloatPosition(null)
    expect($monitorFloatPosition.get()).toBeNull()
  })
})

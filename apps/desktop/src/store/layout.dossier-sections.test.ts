import { beforeEach, describe, expect, it } from 'vitest'

import {
  $dossierCollapsedSections,
  $dossierFloatPosition,
  isDossierSectionCollapsed,
  setDossierFloatPosition,
  toggleDossierSection
} from './layout'

/**
 * 档案卡的**逐段折叠**与**浮层位置**（Qoder 那一层，11-18 10:0x 拍板 B）。
 *
 * Qoder 的任务监控面板有 13 个分区，每个都能单独收起，状态存在
 * `qoder:workbench:task-monitor-section-visibility`；浮层可拖动并记住位置。
 * 我们照抄这两件。
 *
 * 另起一档而不是并进 `layout.test.ts`：那一档归 `WP-DOSSIER-MONITOR` 认领，
 * 同改一档会被登记册判成「跨线相咬」（裁定 77 的认领范围门也会拦下越界提交）。
 */
describe('档案卡的逐段折叠与浮层位置', () => {
  beforeEach(() => {
    $dossierCollapsedSections.set({})
    $dossierFloatPosition.set(null)
  })

  it('折起一段就记着，别段不动', () => {
    toggleDossierSection('stages')

    expect(isDossierSectionCollapsed('stages')).toBe(true)
    expect(isDossierSectionCollapsed('progress')).toBe(false)
  })

  it('再点一次就展开', () => {
    toggleDossierSection('stages')
    toggleDossierSection('stages')

    expect(isDossierSectionCollapsed('stages')).toBe(false)
  })

  it('全部展开时不留一份全 false 的垃圾记录', () => {
    toggleDossierSection('stages')
    toggleDossierSection('stages')

    expect($dossierCollapsedSections.get()).toEqual({})
  })

  it('浮层位置能存能取（拖完记住）', () => {
    setDossierFloatPosition({ x: 120, y: 80 })
    expect($dossierFloatPosition.get()).toEqual({ x: 120, y: 80 })

    setDossierFloatPosition(null)
    expect($dossierFloatPosition.get()).toBeNull()
  })
})

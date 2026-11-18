import { describe, expect, it } from 'vitest'

import { clampFloatPosition } from './floating-position'

/**
 * 浮层拖动后要**夹回窗口内**（Qoder 的 `clampTaskMonitorPosition` 同义件）。
 *
 * 夹不住就会拖到屏幕外找不回来 —— 这一条必须钉住。
 */
describe('clampFloatPosition', () => {
  const bounds = { height: 560, margin: 16, viewportHeight: 900, viewportWidth: 1200, width: 304 }

  it('拖出左上角 ⇒ 拉回 margin', () => {
    expect(clampFloatPosition({ x: -50, y: -80 }, bounds)).toEqual({ x: 16, y: 16 })
  })

  it('拖出右下角 ⇒ 拉回窗口内', () => {
    expect(clampFloatPosition({ x: 5000, y: 5000 }, bounds)).toEqual({
      x: 1200 - 304 - 16,
      y: 900 - 560 - 16
    })
  })

  it('窗口比浮层还小 ⇒ 至少留 margin（不出现负数）', () => {
    const tiny = { height: 560, margin: 16, viewportHeight: 100, viewportWidth: 100, width: 304 }

    expect(clampFloatPosition({ x: 50, y: 50 }, tiny)).toEqual({ x: 16, y: 16 })
  })

  it('窗口内的位置原样保留', () => {
    expect(clampFloatPosition({ x: 200, y: 120 }, bounds)).toEqual({ x: 200, y: 120 })
  })
})

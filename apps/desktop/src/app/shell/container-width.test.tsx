// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useContainerWidth } from './container-width'

/**
 * JS 版的**容器查询**：量一个容器的 inline-size。
 *
 * 用户 11-16 19:4x 拍板：「这一列先只做容器查询（宽度不够就折叠、不遮对话）」。
 * Qoder 那边是纯 CSS（`@container new-chat-b1 (max-width:639.98px)`），我们这边
 * 用 JS 量 —— 档案卡是对话卡的**兄弟**（CSS 容器查询管不到兄弟），而且我们要按
 * 宽度改**点击行为**（窄时点图标弹贴头浮层、宽时点图标切换显隐），纯 CSS 做不到。
 *
 * 这一档钉住的是**量得准**：量到的是容器自己的 inline-size，不是父级的，也不是
 * 猜的常量。
 */
function Probe({ withElement = true }: { withElement?: boolean }) {
  const [element, setElement] = useState<HTMLDivElement | null>(null)
  const width = useContainerWidth(element)

  return (
    <div data-measured={width} data-testid="probe">
      {withElement ? <div data-testid="container" ref={setElement} /> : null}
    </div>
  )
}

function mockRect(width: number) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 600,
    height: 600,
    left: 0,
    right: width,
    top: 0,
    width,
    x: 0,
    y: 0,
    toJSON: () => ({})
  } as DOMRect)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('useContainerWidth', () => {
  it('量出容器自己的 inline-size', () => {
    mockRect(900)

    render(<Probe />)

    expect(screen.getByTestId('probe').getAttribute('data-measured')).toBe('900')
  })

  it('没有容器时宽度是 0（首帧不会被判成窄）', () => {
    mockRect(900)

    render(<Probe withElement={false} />)

    expect(screen.getByTestId('probe').getAttribute('data-measured')).toBe('0')
  })

  it('容器换了会重新量（观察跟着搬家，不守着旧节点）', () => {
    mockRect(640)

    const { rerender } = render(<Probe withElement={false} />)

    expect(screen.getByTestId('probe').getAttribute('data-measured')).toBe('0')

    rerender(<Probe withElement />)

    expect(screen.getByTestId('probe').getAttribute('data-measured')).toBe('640')
  })
})

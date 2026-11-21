import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  anchorForSelection,
  SelectionToolbar,
  type SelectionToolbarAction
} from './selection-toolbar'

/**
 * 选区浮窗（WP-SELECTION-TOOLBAR）。
 *
 * 两组判据：**定位 maths**（纯函数，jsdom 不给真实 Range 报布局，能测的就是它）与
 * **浮窗行为**（选区落在容器里才出现／点它不塌选区／点它动作照走）。
 */

const CONTAINER = { bottom: 600, height: 600, left: 0, top: 0, width: 400 }
const SIZE = { height: 34, width: 132 }

describe('anchorForSelection', () => {
  it('贴着选区下方', () => {
    expect(anchorForSelection(CONTAINER, { bottom: 120, left: 40, right: 200, top: 100 }, SIZE)).toEqual({
      left: 40,
      top: 126
    })
  })

  it('下方放不下就翻到选区上方', () => {
    const short = { bottom: 200, height: 200, left: 0, top: 0, width: 400 }

    expect(anchorForSelection(short, { bottom: 190, left: 40, right: 200, top: 180 }, SIZE)).toEqual({
      left: 40,
      top: 140
    })
  })

  it('不许越过容器的右边', () => {
    const anchor = anchorForSelection(CONTAINER, { bottom: 120, left: 1_000, right: 1_200, top: 100 }, SIZE)

    expect(anchor.left).toBe(400 - 132 - 8)
  })

  it('不许越过容器的下边', () => {
    const short = { bottom: 90, height: 90, left: 0, top: 0, width: 400 }
    const anchor = anchorForSelection(short, { bottom: 500, left: 0, right: 100, top: 480 }, SIZE)

    expect(anchor.top).toBe(90 - 34 - 8)
    expect(anchor.left).toBeGreaterThanOrEqual(8)
  })

  it('容器比浮窗还窄时也不许跑出左边', () => {
    const tiny = { bottom: 60, height: 60, left: 0, top: 0, width: 40 }
    const anchor = anchorForSelection(tiny, { bottom: 20, left: 0, right: 10, top: 10 }, SIZE)

    expect(anchor.left).toBe(8)
    // 容器只有 60 高、浮窗 34 高 ⇒ 浮窗最靠下也只能到 60-34-8=18。
    expect(anchor.top).toBe(18)
  })
})

function selectNode(node: Node) {
  const range = document.createRange()

  range.selectNodeContents(node)

  const selection = window.getSelection()

  selection?.removeAllRanges()
  selection?.addRange(range)
}

function Harness({ onCopy }: { onCopy: (text: string) => void }) {
  const ref = useRef<HTMLDivElement | null>(null)

  const actions: SelectionToolbarAction[] = [
    {
      id: 'copy',
      label: '复制',
      onSelect: ({ text }) => onCopy(text)
    }
  ]

  return (
    <div>
      <p data-testid="outside">别处的一段话</p>
      <div ref={ref}>
        <p data-selectable-text="true">选中这一段话</p>
      </div>
      <SelectionToolbar actions={actions} containerRef={ref} />
    </div>
  )
}

describe('SelectionToolbar', () => {
  afterEach(() => {
    cleanup()
    window.getSelection()?.removeAllRanges()
  })

  it('选区落在容器里，浮窗才出现', () => {
    render(<Harness onCopy={vi.fn()} />)

    selectNode(screen.getByText('选中这一段话'))
    fireEvent.mouseUp(document)

    expect(screen.getByTestId('selection-toolbar')).toBeTruthy()
  })

  it('选区在容器外，浮窗不出现（别把别的面板也认领了）', () => {
    render(<Harness onCopy={vi.fn()} />)

    selectNode(screen.getByTestId('outside'))
    fireEvent.mouseUp(document)

    expect(screen.queryByTestId('selection-toolbar')).toBeNull()
  })

  it('点浮窗不许把选区点塌——否则按「复制」时选中的字已经没了', () => {
    render(<Harness onCopy={vi.fn()} />)

    selectNode(screen.getByText('选中这一段话'))
    fireEvent.mouseUp(document)

    const button = screen.getByTestId('selection-toolbar-copy')

    // `fireEvent` 返回 false ⇔ 事件被 preventDefault 过。
    expect(fireEvent.mouseDown(button)).toBe(false)
    expect(window.getSelection()?.toString()).toContain('选中这一段话')
  })

  it('点动作时把选中的那一段交出去', () => {
    const onCopy = vi.fn()

    render(<Harness onCopy={onCopy} />)

    selectNode(screen.getByText('选中这一段话'))
    fireEvent.mouseUp(document)

    fireEvent.click(screen.getByTestId('selection-toolbar-copy'))

    expect(onCopy).toHaveBeenCalledWith('选中这一段话')
  })

  it('动作走完浮窗就收起', () => {
    render(<Harness onCopy={vi.fn()} />)

    selectNode(screen.getByText('选中这一段话'))
    fireEvent.mouseUp(document)

    expect(screen.getByTestId('selection-toolbar')).toBeTruthy()

    fireEvent.click(screen.getByTestId('selection-toolbar-copy'))

    expect(screen.queryByTestId('selection-toolbar')).toBeNull()
  })
})

/** webview 那一档：选区由宿主读出来从外部喂进来（WP-OFFICE-SELECTION-TOOLBAR）。 */
describe('SelectionToolbar external', () => {
  afterEach(cleanup)

  const RECT = { bottom: 120, left: 40, right: 200, top: 100 }

  function ExternalHarness({
    onCopy,
    onDismiss,
    external
  }: {
    external: null | { rect: typeof RECT; text: string }
    onCopy: (text: string) => void
    onDismiss: () => void
  }) {
    const ref = useRef<HTMLDivElement | null>(null)
    const actions: SelectionToolbarAction[] = [
      { id: 'copy', label: '复制', onSelect: ({ text }) => onCopy(text) }
    ]

    return (
      <div ref={ref}>
        <SelectionToolbar
          actions={actions}
          containerRef={ref}
          external={external}
          onExternalDismiss={onDismiss}
          testId="external-toolbar"
        />
      </div>
    )
  }

  it('external 有值时浮窗出现，不靠 window selection', () => {
    render(
      <ExternalHarness
        external={{ rect: RECT, text: 'webview 里选的字' }}
        onCopy={vi.fn()}
        onDismiss={vi.fn()}
      />
    )

    expect(screen.getByTestId('external-toolbar')).toBeTruthy()
  })

  it('动作拿到 external 的文字，并通知外部清源', () => {
    const onCopy = vi.fn()
    const onDismiss = vi.fn()

    render(
      <ExternalHarness
        external={{ rect: RECT, text: 'webview 里选的字' }}
        onCopy={onCopy}
        onDismiss={onDismiss}
      />
    )
    fireEvent.click(screen.getByTestId('external-toolbar-copy'))

    expect(onCopy).toHaveBeenCalledWith('webview 里选的字')
    expect(onDismiss).toHaveBeenCalledOnce()
  })

  it('external 变 null 时浮窗收起', () => {
    const { rerender } = render(
      <ExternalHarness
        external={{ rect: RECT, text: 'webview 里选的字' }}
        onCopy={vi.fn()}
        onDismiss={vi.fn()}
      />
    )

    expect(screen.getByTestId('external-toolbar')).toBeTruthy()

    rerender(<ExternalHarness external={null} onCopy={vi.fn()} onDismiss={vi.fn()} />)

    expect(screen.queryByTestId('external-toolbar')).toBeNull()
  })
})

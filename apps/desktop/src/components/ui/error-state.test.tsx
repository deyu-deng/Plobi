/**
 * 错误卡 v2 的行为契约：严重度 + 一句话 + **一个**动作 + 可复制的详情。
 *
 * 三条要钉住的（都是用户对界面的原话推出来的）：
 * 1. 标题是人话；**原始详情默认收起**，点开才出现（屏幕不当日志用）。
 * 2. 详情能一键复制（修它的人要原文），复制成功要有反馈。
 * 3. 只允许一个动作——两个动作等于没动作（类型上就只有一个 `action`）。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ErrorCard } from './error-state'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ErrorCard', () => {
  it('says what went wrong in one line and keeps the raw detail closed', () => {
    render(<ErrorCard detail={'Traceback (most recent call last):\n  File "x.py", line 1'} title="上一次没跑成" />)

    expect(screen.getByText('上一次没跑成')).toBeTruthy()
    expect(screen.queryByText(/Traceback/)).toBeNull()

    fireEvent.click(screen.getByText('Details'))
    expect(screen.getByText(/Traceback/)).toBeTruthy()

    fireEvent.click(screen.getByText('Hide details'))
    expect(screen.queryByText(/Traceback/)).toBeNull()
  })

  it('copies the detail and says so', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(<ErrorCard detail="provider returned 500" title="没跑成" />)
    fireEvent.click(screen.getByText('Copy details'))

    expect(writeText).toHaveBeenCalledWith('provider returned 500')
    expect(await screen.findByText('Copied')).toBeTruthy()
  })

  it('renders exactly one action when given', () => {
    const onClick = vi.fn()
    render(<ErrorCard action={{ label: '立即触发', onClick }} detail="x" title="没跑成" />)

    fireEvent.click(screen.getByText('立即触发'))
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(screen.getAllByRole('button').filter(node => node.textContent === '立即触发')).toHaveLength(1)
  })

  it('keeps warning and error visually distinct without changing the words', () => {
    const { container } = render(<ErrorCard severity="warning" title="额度快用完了" />)
    const card = container.querySelector('[data-slot="error-card"]')
    expect(card?.getAttribute('data-severity')).toBe('warning')
    expect(card?.getAttribute('role')).toBe('status')
    expect(screen.getByText('额度快用完了')).toBeTruthy()
  })
})

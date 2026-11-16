/**
 * 对话卡抬头那一枚「等你点头（写脑）」入口（`WP-MIND-CONSENT-EVENTS`）。
 *
 * 用户 11-16 21:4x 拍定搬进对话卡（规格 §8 第 2 条），左栏底部那颗角标由本刀删掉。
 * 这一档钉三件事：点了真能打开那一屏、角标只数**批得动**的、没货时不出数字。
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

const { MindPendingButton } = await import('./mind-pending-button')
const { $mindPending, $mindPendingOpen } = await import('@/store/mind-pending')
const { en } = await import('@/i18n/en')

const item = (id: string, expired = false) => ({
  expired,
  id,
  kind: 'mind_plan',
  paths: [`wiki/${id}.md`],
  summary: '1 项',
})

const fill = (items: ReturnType<typeof item>[], expiredCount = 0) =>
  $mindPending.set({
    auto: false,
    count: items.length,
    expired_count: expiredCount,
    gate: true,
    items
  })

describe('对话卡抬头的写脑入口', () => {
  beforeEach(() => {
    $mindPendingOpen.set(false)
    fill([])
  })

  afterEach(() => {
    cleanup()
    $mindPendingOpen.set(false)
  })

  it('点一下就是打开那一屏', () => {
    render(<MindPendingButton />)

    fireEvent.click(screen.getByRole('button', { name: en.mindPending.title }))

    expect($mindPendingOpen.get()).toBe(true)
  })

  it('角标只数还批得动的：过期那张不算', () => {
    fill([item('a'), item('b'), item('stale', true)], 1)
    render(<MindPendingButton />)

    expect(screen.getByText('2')).toBeTruthy()
  })

  it('没货时不出数字，也不显示假的 0', () => {
    fill([])
    render(<MindPendingButton />)

    expect(screen.queryByText('0')).toBeNull()
  })

  it('两位数的计数收成 9+，别把抬头撑开', () => {
    fill(Array.from({ length: 12 }, (_, i) => item(`p${i}`)))
    render(<MindPendingButton />)

    expect(screen.getByText('9+')).toBeTruthy()
  })
})

import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { hasSourceIcon, SourceAvatar } from './source-icon'

describe('the source icons', () => {
  it('gives the four built-in sources a mark of their own', () => {
    // 「不能只写名字」——四条各一个，不许退回首字母那一支。
    for (const sourceId of ['wechat', 'dingtalk', 'timetable', 'zju']) {
      expect(hasSourceIcon(sourceId), sourceId).toBe(true)
    }
  })

  it('paints a brand mark for WeChat', () => {
    const { container } = render(<SourceAvatar displayName="WeChat" sourceId="wechat" />)

    expect(container.querySelector('svg')).toBeTruthy()
    // 品牌色 16% 底染 —— 与网关那一族同一个比例，字形在任何底色上都读得出来。
    expect(container.querySelector('span')?.getAttribute('style') ?? '').toContain('color-mix')
  })

  it('falls back to a letter monogram for DingTalk, in its brand colour', () => {
    // 钉钉**没有**品牌字形：品牌方要求从 Simple Icons 下架。所以走字母徽标，
    // 这不是将就 —— 网关那边的 Slack 是同一个做法。
    const { container } = render(<SourceAvatar displayName="DingTalk" sourceId="dingtalk" />)
    const badge = container.querySelector('span')

    expect(badge?.textContent).toBe('D')
    expect(badge?.getAttribute('style') ?? '').toContain('rgb(50, 150, 250)')
    expect(container.querySelector('svg')).toBeNull()
  })

  it('gives a third-party source a letter rather than nothing', () => {
    // 第三方包（多半）不在这张表里。**这不是错误**：一个首字母徽标仍然是一个能看的答案，
    // 而给装饰建一条跨仓的声明通道不值那个代价。
    const { container } = render(<SourceAvatar displayName="Telegram" sourceId="some-new-collector" />)
    const badge = container.querySelector('span')

    expect(hasSourceIcon('some-new-collector')).toBe(false)
    expect(badge?.textContent).toBe('T')
    expect(badge?.getAttribute('style') ?? '').not.toContain('color-mix')
  })

  it('takes the letter from the display name, not the id', () => {
    // 退回首字母时用**显示名**：id 是内部的，显示名是给用户看的那个。
    const { container } = render(<SourceAvatar displayName="学术（浙大）" sourceId="zju-extra" />)

    expect(container.querySelector('span')?.textContent).toBe('学')
  })

  it('never renders an empty badge for a nameless source', () => {
    const { container } = render(<SourceAvatar displayName="   " sourceId="anon" />)

    expect(container.querySelector('span')?.textContent).toBe('?')
  })

  it('is hidden from assistive tech, because the name right next to it already says it', () => {
    // 图标是 aria-hidden：读屏软件该念的是「微信」不是「一个图标」。
    const { container } = render(<SourceAvatar displayName="WeChat" sourceId="wechat" />)

    // 包住整个图标（含品牌字形自带的 <title>）都是 aria-hidden，读屏只念旁边的名字。
    expect(container.querySelector('span')?.getAttribute('aria-hidden')).toBe('true')
  })
})
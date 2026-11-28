// @vitest-environment jsdom
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PLUGIN_VIEW_PREFIX, viewRenderer } from '@/app/views'

import { parsePluginMessage, PluginFrame, pluginFrameSrc } from './plugin-frame'

/**
 * 插件的界面跑在**独立 origin** 的 iframe 里（深档机制第三刀 · 规格
 * ``Docs/specs/plugin-sandbox.md`` §2.3）。
 *
 * 判据钉的是**关系**：那个 iframe **没有同源特权**、**只认自己那个窗口**发来的消息、
 * **认不出的消息形状当场丢掉**；而 ``view: plugin:<目录名>`` ⇒ 宿主把那个插件的界面
 * 装进这一格。
 */
describe('插件界面的那个 iframe', () => {
  it('**没有同源特权**（sandbox 只给 allow-scripts，不给 allow-same-origin）', () => {
    render(<PluginFrame dir="my-thing" />)

    const frame = document.querySelector('iframe') as HTMLIFrameElement

    expect(frame).toBeTruthy()
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin')
    // 独立 origin：不是 file://，也不是宿主那个 origin。
    expect(frame.getAttribute('src')).toBe('plobi-plugin://my-thing/index.html')
  })

  it('入口可以换（`pluginFrameSrc`）', () => {
    expect(pluginFrameSrc('my-thing')).toBe('plobi-plugin://my-thing/index.html')
    expect(pluginFrameSrc('my-thing', 'panel.html')).toBe('plobi-plugin://my-thing/panel.html')
  })

  it('**只认自己那个 iframe 窗口**发来的消息（别的窗口发的一律不理）', () => {
    const onMessage = vi.fn()
    render(<PluginFrame dir="my-thing" onMessage={onMessage} />)

    // 别的窗口（不是我们那个 iframe）⇒ 不理
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: 'plobi:ready' }, source: window as unknown as Window })
    )
    expect(onMessage).not.toHaveBeenCalled()
  })

  it('认得的形状才收（认不出的当场丢掉）', () => {
    expect(parsePluginMessage({ type: 'plobi:ready' })).toEqual({ type: 'plobi:ready' })
    expect(parsePluginMessage({ type: 'plobi:request', id: 'a', payload: { x: 1 } })).toEqual({
      type: 'plobi:request',
      id: 'a',
      payload: { x: 1 }
    })
    // 认不出的类型／不是对象／空 ⇒ 丢掉
    expect(parsePluginMessage({ type: 'something.else' })).toBeNull()
    expect(parsePluginMessage({})).toBeNull()
    expect(parsePluginMessage(null)).toBeNull()
    expect(parsePluginMessage('ready')).toBeNull()
    expect(parsePluginMessage([{ type: 'plobi:ready' }])).toBeNull()
  })
})

describe('`view: plugin:<目录名>` ⇒ 宿主把那个插件的界面装进这一格', () => {
  it('认得插件那个前缀', () => {
    const Renderer = viewRenderer(`${PLUGIN_VIEW_PREFIX}my-thing`)

    expect(Renderer).toBeTruthy()

    if (!Renderer) {
      return
    }

    render(<Renderer />)
    expect(document.querySelector('iframe')?.getAttribute('src')).toBe('plobi-plugin://my-thing/index.html')
  })

  it('目录名里带分隔符 ⇒ 不认（不许借它跑到别处去）', () => {
    expect(viewRenderer(`${PLUGIN_VIEW_PREFIX}../..`)).toBeUndefined()
    expect(viewRenderer(`${PLUGIN_VIEW_PREFIX}a/b`)).toBeUndefined()
    expect(viewRenderer(`${PLUGIN_VIEW_PREFIX}`)).toBeUndefined()
  })

  it('不认识的那个 view 照旧不渲染', () => {
    expect(viewRenderer('not-a-view')).toBeUndefined()
  })
})

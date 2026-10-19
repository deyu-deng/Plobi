/**
 * 这一档测的是**取数那一层本身**，不是组件。
 *
 * 为什么单独一档、而且 mock 的是 `window.plobiDesktop.api` 而不是 `./sources/api`：
 * 第一版把 `getSources` 整个 mock 掉，喂给它自己发明的形状，于是**怎么错的都测不出来**。
 * 真的那次崩溃是：`window.plobiDesktop.api` 返回的是**整个信封** `{ok, data}`（因为
 * electron 的 `fetchJson` 只 JSON.parse、**不解包**），而 `getSources` 把它当成了载荷，
 * 于是下游 `payload.categories.map` 炸在一行离原因十万八里的地方。
 *
 * 所以这里 mock 的是**真的边界**（那个 ipc 桥），喂进去的是**后端真的回的那个信封**。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getExternalPackages,
  getSources,
  installExternalPackage,
  removeExternalPackage,
  saveSourceSetup,
  setSourceEnabled,
  trySource,
  unwrapData
} from './api'

const api = vi.fn()

vi.stubGlobal('window', {
  plobiDesktop: {
    api: (request: unknown) => api(request)
  }
})

/** 后端真的回的那个信封（`plobi/collectors/sources_api.py` 逐字）。 */
function envelope(data: unknown) {
  return { data, ok: true }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('the envelope', () => {
  it('is what the desktop bridge hands back, not the payload', async () => {
    // 这一条钉的是**前提**：ipc 桥不解包。所以下面每一条都必须自己解。
    api.mockResolvedValue(envelope({ categories: [], sources: [] }))

    const payload = await getSources()

    expect(payload).toEqual({ categories: [], sources: [] })
  })

  it('throws with the expected shape rather than handing back a hollow object', () => {
    // 静默给一个空对象 ⇒ 下游拿到 undefined.sources ⇒ 崩在 `.map` 上，而真正的原因
    // 在这里。崩的地方永远不是出问题的地方。
    expect(() => unwrapData({ categories: [] }, '/api/collect/sources')).toThrow(/no "data"/)
    expect(() => unwrapData(null, '/api/collect/sources')).toThrow(/no "data"/)
  })

  it('says "restart the backend" because that is the usual cause', () => {
    // 后端是另一个进程，改了它不会跟着变——所以那句提示是有用的，不是客套。
    expect(() => unwrapData({}, '/api/collect/sources')).toThrow(/restart it/)
  })

  it('passes a real envelope through untouched', () => {
    expect(unwrapData({ ok: true, data: { a: 1 } }, '/x')).toEqual({ a: 1 })
  })
})

describe('every call unwraps', () => {
  it('reads the sources list', async () => {
    api.mockResolvedValue(envelope({ categories: [{ id: 'message', sources: ['wechat'] }], sources: [] }))

    const payload = await getSources()

    expect(payload.categories).toEqual([{ id: 'message', sources: ['wechat'] }])
  })

  it('reads a try receipt', async () => {
    api.mockResolvedValue(envelope({ detail: 'no_builder 那一整句', ok: false, source: 'dingtalk', state: 'no_builder' }))

    expect(await trySource('dingtalk')).toMatchObject({ detail: 'no_builder 那一整句', state: 'no_builder' })
  })

  it('reads the external package list, tolerating an empty one', async () => {
    api.mockResolvedValue(envelope({ packages: [] }))

    expect(await getExternalPackages()).toEqual([])
  })

  it('reads the action name out of a spawn receipt', async () => {
    api.mockResolvedValue(envelope({ name: 'collectors-install-abc' }))

    expect(await installExternalPackage('x')).toBe('collectors-install-abc')
  })

  it('reads the action name out of a removal receipt', async () => {
    api.mockResolvedValue(envelope({ name: 'collectors-remove-abc' }))

    expect(await removeExternalPackage('x')).toBe('collectors-remove-abc')
  })

  it('posts the right paths and bodies', async () => {
    api.mockResolvedValue(envelope({}))

    await setSourceEnabled('wechat', false)
    await saveSourceSetup('timetable', { ics_path: 'D:/x.ics' })
    await trySource('zju')

    expect(api.mock.calls.map(call => call[0].path)).toEqual([
      '/api/collect/sources/wechat/enabled',
      '/api/collect/sources/timetable/setup',
      '/api/collect/sources/zju/try'
    ])
    expect(api.mock.calls[0][0].body).toEqual({ enabled: false })
    expect(api.mock.calls[1][0].body).toEqual({ params: { ics_path: 'D:/x.ics' } })
  })

  it('escapes the id in the path rather than pasting it in', async () => {
    // 一个带斜杠的 id 若不转义，就能把请求打到另一个路由上。
    api.mockResolvedValue(envelope({}))

    await setSourceEnabled('a/b', true)

    expect(api.mock.calls[0][0].path).toBe('/api/collect/sources/a%2Fb/enabled')
  })
})
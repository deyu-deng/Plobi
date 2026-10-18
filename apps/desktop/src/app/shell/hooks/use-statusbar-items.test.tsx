import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { DesktopDeferredSubsystem } from '@/global'
import { TRANSLATIONS } from '@/i18n/catalog'

import { DeferredLedgerRow } from './use-statusbar-items'

/**
 * WP-DEFERRED-PLAINTEXT-RENDER 判据①③：状态栏那一行的四段里要有一句人话，
 * 且**不许再出现 `deferred` / `red` 这种状态码字面**。
 *
 * 行是**注入的假读数**（裁 176：绿时不显示才是正确形状）——不许为了验收去把这台
 * 真跑着的 sidecar 弄坏，所以这里不碰探针、不碰主进程，只喂一条形状正确的行。
 */
const zh = TRANSLATIONS.zh
const copy = zh.boot.sidecars

function row(overrides: Partial<DesktopDeferredSubsystem> & { id: string; state: DesktopDeferredSubsystem['state'] }): DesktopDeferredSubsystem {
  return {
    detail: '注入的假读数，不代表这台的真状态',
    enableHint: '把 sidecar 起来监听 :5030',
    name: { en: 'chatlog sidecar', zh: '微信采集 chatlog' },
    observed: '没在 :5030 上监听',
    port: 5030,
    timestamp: 0,
    ...overrides
  } as DesktopDeferredSubsystem
}

afterEach(cleanup)

function textOf(r: DesktopDeferredSubsystem): string {
  const view = render(<DeferredLedgerRow copy={copy} row={r} />)

  return view.getByTestId(`deferred-row-${r.id}`).textContent ?? ''
}

describe('状态栏那一行说人话（WP-DEFERRED-PLAINTEXT-RENDER）', () => {
  it('红那一档：说「试过，没起来」，屏上不出现 red 这个字面', () => {
    const text = textOf(row({ id: 'chatlog-red', state: 'red' }))

    expect(text).toContain(copy.stateRed)
    expect(text).not.toMatch(/\bred\b/)
    expect(text).not.toMatch(/\bdeferred\b/)
  })

  it('后置那一档：说「这一波还没接」，屏上不出现 deferred 这个字面', () => {
    const text = textOf(row({ id: 'chatlog-deferred', state: 'deferred' }))

    expect(text).toContain(copy.stateDeferred)
    expect(text).not.toMatch(/\bdeferred\b/)
    expect(text).not.toMatch(/\bred\b/)
  })

  it('四段里除了名字，还带着「观察到什么」和「怎么回来」两句人话', () => {
    const view = render(<DeferredLedgerRow copy={copy} row={row({ id: 'aigw-x', state: 'red' })} />)
    const text = view.getByTestId('deferred-row-aigw-x').textContent ?? ''

    // 名字那一格由 `deferredRowLabel` 按当前语言挑（jsdom 默认 en），两种都该是同一个 sidecar
    expect(text).toMatch(/chatlog/)
    expect(text).toContain('没在 :5030 上监听')
    expect(text).toContain(`${copy.enableLabel}: 把 sidecar 起来监听 :5030`)
  })

  it('zh 那句旧理由与已判掉的编号都不在这份文案里', () => {
    expect(copy.deferredHint).not.toContain('根本没有可以启动的东西')
    expect(copy.deferredHint).not.toMatch(/R-0\d\d/)
    expect(copy.deferredTitle).not.toMatch(/R-0\d\d/)
  })
})

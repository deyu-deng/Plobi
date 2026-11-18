import { beforeEach, describe, expect, it } from 'vitest'

import { setRuntimeI18nLocale } from '@/i18n/runtime'
import { $connectedDesktopApps, markConnected } from '@/store/desktop-quotas'
import type { ModelOptionProvider } from '@/types/plobi'

import { mainProviderRows } from './provider-rows'

// 11-18 第三处之后，后端一行一家软件：slug 就是软件名，模型 id 是 `<软件>/<模型>`。
const catalog: ModelOptionProvider[] = [
  { models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' },
  { models: ['workbuddy/hy3'], name: 'WorkBuddy', slug: 'workbuddy' },
  { models: ['antigravity/gemini-3-pro'], name: 'Antigravity', slug: 'antigravity' }
]

describe('mainProviderRows', () => {
  beforeEach(() => {
    $connectedDesktopApps.set({})
    setRuntimeI18nLocale('en')
  })

  it('只会有一行中性名兜底：一帧都还没到、清单里没有在册软件行时', () => {
    const rows = mainProviderRows([{ models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' }])

    expect(rows.map(row => row.name)).toEqual(['Z.AI', 'Subscriptions'])
    expect(rows.find(row => row.name === 'Subscriptions')?.ui_key).toBe('hub')
  })

  it('兜底那行的名字跟界面语言走，不是写死的中文', () => {
    // 那一行原来写死「订阅额度」——日文与繁中界面上也是中文。
    setRuntimeI18nLocale('zh')

    try {
      const rows = mainProviderRows([{ models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' }])

      expect(rows.map(row => row.name)).toEqual(['Z.AI', '订阅额度'])
    } finally {
      setRuntimeI18nLocale('en')
    }
  })

  it('一帧都还没到（apps 表是空的）时，后端发来的软件行原样留着，不塞兜底行', () => {
    // 第一帧之前 `$connectedDesktopApps` 是空的：这时不能把后端已经裁过的行丢掉，
    // 否则选择器会比后端知道得还少。兜底行只在「清单里一条软件行都没有」时出现。
    const rows = mainProviderRows(catalog)

    expect(rows.map(row => row.slug)).toEqual(['zai', 'workbuddy', 'antigravity'])
  })

  it('连上的软件各自一行，行的身份就是它自己的 slug', () => {
    markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })

    const rows = mainProviderRows(catalog)
    const workbuddy = rows.find(row => row.slug === 'workbuddy')

    expect(workbuddy?.name).toBe('WorkBuddy')
    expect(workbuddy?.ui_key).toBe('workbuddy')
    // 这一行收窄到网关认可的那几个 id：帧里只报了 `hy3`。
    expect(workbuddy?.models).toEqual(['workbuddy/hy3'])
  })

  it('同一家的两条腿各占一行，靠 slug 天然分得开', () => {
    markConnected({ id: 'workbuddy', models: ['hy3'], name: 'WorkBuddy' })
    markConnected({ id: 'workbuddy_api', models: ['hy3'], name: 'WorkBuddy' })

    const bothLegs: ModelOptionProvider[] = [
      { models: ['glm-5.2'], name: 'Z.AI', slug: 'zai' },
      { models: ['workbuddy/hy3'], name: 'WorkBuddy', slug: 'workbuddy' },
      { models: ['workbuddy_api/hy3'], name: 'WorkBuddy', slug: 'workbuddy_api' }
    ]

    const rows = mainProviderRows(bothLegs).filter(
      row => row.slug === 'workbuddy' || row.slug === 'workbuddy_api'
    )

    expect(rows).toHaveLength(2)
    expect(rows.map(row => row.name)).toEqual(['WorkBuddy', 'WorkBuddy'])
    expect(new Set(rows.map(row => row.ui_key))).toEqual(new Set(['workbuddy', 'workbuddy_api']))
  })
})

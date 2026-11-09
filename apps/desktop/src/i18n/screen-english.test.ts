import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ja } from './ja'
import { zh } from './zh'
import { zhHant } from './zh-hant'

/**
 * 屏上英文残留的判据（刀 B `WP-UI-EN-LEFT`）。
 *
 * 这一族坏法不是「少翻译」，是**英文直接写在组件里**——日文与繁中界面上那一行
 * 就是英文，看的人拿不到指引。这里钉两件事：
 *
 *   ① 那七个屏不许再出现硬编英文（源码级扫描，逐句点名）；
 *   ② 搬进目录的那些句子在四语里都有一句人话，而且日文与繁中**不是英文的回落**
 *      （`defineLocale` 缺键时会回落英文，所以「有键」不等于「翻过」）。
 */

const CATALOGS: Array<[string, unknown]> = [
  ['en', en],
  ['zh', zh],
  ['ja', ja],
  ['zh-hant', zhHant]
]

/** 本刀搬进目录的每一句（点路径）。 */
const MOVED_KEYS = [
  // settings.uninstall —— 卸载那一屏
  'settings.uninstall.guiTitle',
  'settings.uninstall.guiDescription',
  'settings.uninstall.guiConsequence',
  'settings.uninstall.liteTitle',
  'settings.uninstall.liteDescription',
  'settings.uninstall.liteConsequence',
  'settings.uninstall.fullTitle',
  'settings.uninstall.fullDescription',
  'settings.uninstall.fullConsequence',
  'settings.uninstall.dangerZone',
  'settings.uninstall.checking',
  'settings.uninstall.confirmTitle',
  'settings.uninstall.confirmBody',
  'settings.uninstall.appPath',
  'settings.uninstall.yesUninstall',
  'settings.uninstall.uninstalling',
  'settings.uninstall.cancel',
  'settings.uninstall.sectionTitle',
  'settings.uninstall.sectionHint',
  'settings.uninstall.startFailed',
  // settings.computerUse —— computer use 面板
  'settings.computerUse.linuxNote',
  'settings.computerUse.win32Note',
  'settings.computerUse.waitingApproval',
  'settings.computerUse.grantPermissions',
  'settings.computerUse.checkingStatus',
  'settings.computerUse.statusReadFailed',
  'settings.computerUse.requestFailed',
  'settings.computerUse.approveTitle',
  'settings.computerUse.approveMessage',
  'settings.computerUse.unsupported',
  'settings.computerUse.installHint',
  'settings.computerUse.installHintGrant',
  'settings.computerUse.identityNote',
  'settings.computerUse.recheck',
  'settings.computerUse.granted',
  'settings.computerUse.notGranted',
  'settings.computerUse.unknown',
  'settings.computerUse.driverHealth',
  'settings.computerUse.ready',
  'settings.computerUse.notReady',
  'settings.computerUse.readyHint',
  'settings.computerUse.accessibilityLabel',
  'settings.computerUse.accessibilityHint',
  'settings.computerUse.screenLabel',
  'settings.computerUse.screenHint',
  // settings.memory —— 记忆供应商的连接那一格
  'settings.memory.connect',
  'settings.memory.connectViaOauth',
  'settings.memory.reconnect',
  'settings.memory.apiKeySet',
  'settings.memory.oauthSet',
  'settings.memory.waitingConsent',
  'settings.memory.startFailed',
  'settings.memory.connectFailed',
  'settings.memory.timedOut',
  'settings.memory.connectionFailed',
  // 记忆图谱的图例
  'starmap.legendSkill',
  'starmap.legendMemory',
  'starmap.legendAge',
  // 外观屏的搜索框与空态
  'settings.appearance.themeSearchPlaceholder',
  'settings.appearance.noInstalledMatch',
  // 宠物生成的参考图按钮
  'commandCenter.generatePet.addReference'
]

/** 消息平台那一族的接入说明：一个平台一句，四语都得有。 */
const PLATFORM_IDS = [
  'telegram',
  'discord',
  'slack',
  'mattermost',
  'matrix',
  'signal',
  'whatsapp',
  'bluebubbles',
  'homeassistant',
  'email',
  'sms',
  'dingtalk',
  'feishu',
  'wecom',
  'wecom_callback',
  'weixin',
  'qqbot',
  'api_server',
  'webhook'
]

/**
 * 那七个屏上不许再出现的英文字面量。
 *
 * 单个词的都带引号——不然 `u.notGranted` 这种属性名会被当成残留。
 */
const SWEPT: Array<{ file: string; literals: string[] }> = [
  {
    file: '../app/settings/uninstall-section.tsx',
    literals: [
      "'Uninstall Chat GUI only'",
      "'Uninstall GUI + agent, keep my data'",
      "'Uninstall everything'",
      "'Danger zone'",
      "'Confirm uninstall'",
      "'Yes, uninstall'",
      "'Uninstall Plobi'",
      "'Uninstall could not start.'",
      'This removes',
      'Choose how much to remove'
    ]
  },
  {
    file: '../app/settings/computer-use-panel.tsx',
    literals: [
      "'Granted'",
      "'Not granted'",
      "'Unknown'",
      "'Ready'",
      "'Not ready'",
      "'Driver health'",
      "'Recheck'",
      "'Grant permissions'",
      "'Waiting for approval",
      "'Checking Computer Use status",
      "'Could not read Computer Use status'",
      "'Could not request permissions'",
      "'Approve in System Settings'",
      'Computer Use is ready',
      'macOS will show a permission dialog',
      'no permission prompt',
      'SmartScreen prompt'
    ]
  },
  {
    file: '../app/settings/memory/connect.tsx',
    literals: [
      "'Could not start the connection.'",
      "'Failed to start connection'",
      "'Timed out",
      "'Connection failed.'",
      "'Connect via OAuth'",
      "'Reconnect'",
      "'api key set'",
      "'oauth set'",
      "'Waiting for browser consent"
    ]
  },
  { file: '../app/starmap/star-map.tsx', literals: ['core = oldest', 'outer = newer'] },
  { file: '../app/messaging/index.tsx', literals: ['PLATFORM_INTRO', '@BotFather'] },
  { file: '../app/pet-generate/pet-generate-content.tsx', literals: ['Add a reference'] },
  {
    file: '../app/settings/appearance-settings.tsx',
    literals: ['Search your themes', 'No installed themes match']
  }
]

function pick(catalog: unknown, path: string): unknown {
  return path
    .split('.')
    .reduce<unknown>(
      (current, part) =>
        current && typeof current === 'object' ? (current as Record<string, unknown>)[part] : undefined,
      catalog
    )
}

function text(catalog: unknown, path: string): string {
  const value = pick(catalog, path)

  if (typeof value === 'function') {
    return (value as (...args: unknown[]) => string)('x')
  }

  return typeof value === 'string' ? value : ''
}

describe('屏上英文残留（刀 B WP-UI-EN-LEFT）', () => {
  it.each(SWEPT)('$file 里不再有硬编英文', ({ file, literals }) => {
    const source = readFileSync(resolve(import.meta.dirname ?? '', file), 'utf-8')
    const hits = literals.filter(literal => source.includes(literal))

    expect(hits, `${file} 里又出现了硬编英文`).toEqual([])
  })

  it.each(CATALOGS)('%s：搬进来的每一句都有内容', (_name, catalog) => {
    const empty = MOVED_KEYS.filter(path => text(catalog, path).trim().length === 0)

    expect(empty, '这些句子在目录里是空的').toEqual([])
  })

  it.each([
    ['zh', zh],
    ['ja', ja],
    ['zh-hant', zhHant]
  ])('%s：搬进来的每一句都翻过了，不是英文回落', (_name, catalog) => {
    const same = MOVED_KEYS.filter(path => text(catalog, path) === text(en, path))

    expect(same, '这些句子和英文一模一样，等于没翻').toEqual([])
  })

  it.each(CATALOGS)('%s：消息平台的接入说明一个平台一句', (_name, catalog) => {
    const intros = pick(catalog, 'messaging.platformIntro') as Record<string, string> | undefined
    const missing = PLATFORM_IDS.filter(id => !intros?.[id]?.trim())

    expect(missing, '这些平台没有接入说明').toEqual([])
  })

  it.each([
    ['zh', zh],
    ['ja', ja],
    ['zh-hant', zhHant]
  ])('%s：消息平台的接入说明翻过了，不是英文回落', (_name, catalog) => {
    const mine = pick(catalog, 'messaging.platformIntro') as Record<string, string>
    const base = pick(en, 'messaging.platformIntro') as Record<string, string>
    const same = PLATFORM_IDS.filter(id => mine[id] === base[id])

    expect(same, '这些平台的接入说明和英文一模一样，等于没翻').toEqual([])
  })

  it('扫到 0 条也算这条测试没牙：目录不是空的，键表也不是空的', () => {
    expect(MOVED_KEYS.length).toBeGreaterThan(50)
    expect(PLATFORM_IDS.length).toBe(19)
    expect(text(en, 'settings.uninstall.sectionTitle')).not.toBe('')
  })
})

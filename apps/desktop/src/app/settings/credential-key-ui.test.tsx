// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { translateNow } from '@/i18n'
import type { EnvProbeResult, EnvVarInfo } from '@/types/plobi'

import { KeyField, type KeyRowProps } from './credential-key-ui'

/**
 * 凭证行那颗「测试」钮。
 *
 * 钉四件事：
 * 1. 后端没登记探测的键，压根不出按钮——不摆一颗点了只会回「不支持」的钮；
 * 2. 点了就把**眼下草稿**那一串交上去（不是已存的那串），这样填错时当场就知道；
 * 3. 测绘中按钮停用，不能连点；
 * 4. 判定分三档：可用、被拒（红）、连不上（灰）——连不上不是「密钥错」。
 */

const KEY = 'PLOBI_TRANSLATE_DEEPL_KEY'

const t = (path: string) => translateNow(`settings.credentials.${path}`)

function makeInfo(overrides: Partial<EnvVarInfo> = {}): EnvVarInfo {
  return { name: KEY, is_password: true, is_set: true, ...overrides } as unknown as EnvVarInfo
}

function makeRowProps(overrides: Partial<KeyRowProps> = {}): KeyRowProps {
  return {
    edits: { [KEY]: 'draft-key' },
    revealed: {},
    saving: null,
    setEdits: vi.fn(),
    onSave: vi.fn(),
    onClear: vi.fn(),
    onReveal: vi.fn(),
    ...overrides
  }
}

function renderField(overrides: {
  info?: Partial<EnvVarInfo>
  props?: Partial<KeyRowProps>
  expanded?: boolean
}) {
  return render(
    <KeyField
      expanded={overrides.expanded ?? true}
      info={makeInfo(overrides.info)}
      rowProps={makeRowProps(overrides.props)}
      varKey={KEY}
    />
  )
}

const verdict = (result: Partial<EnvProbeResult>): Record<string, EnvProbeResult> => ({
  [KEY]: { message: '', ok: false, reachable: true, ...result }
})

afterEach(cleanup)

describe('KeyField 的测试按钮', () => {
  it('没有登记探测的键不出按钮', () => {
    renderField({ props: { onProbe: vi.fn() } })
    expect(screen.queryByLabelText(t('test'))).toBeNull()
  })

  it('有 onProbe 才出按钮，点了把草稿交上去', () => {
    const onProbe = vi.fn()
    renderField({ info: { probe: true }, props: { onProbe } })
    fireEvent.click(screen.getByLabelText(t('test')))
    expect(onProbe).toHaveBeenCalledWith(KEY)
  })

  it('测试中按钮停用', () => {
    renderField({ info: { probe: true }, props: { onProbe: vi.fn(), testing: KEY } })
    expect((screen.getByLabelText(t('test')) as HTMLButtonElement).disabled).toBe(true)
  })

  it('通过的判定写成"可用"', () => {
    renderField({
      info: { probe: true },
      props: { onProbe: vi.fn(), probeResults: verdict({ message: 'ok', ok: true, sample: '你好' }) }
    })
    expect(screen.getByText(new RegExp(t('testOk')))).toBeTruthy()
  })

  it('被服务端拒掉的判定写成"被拒绝"', () => {
    renderField({
      info: { probe: true },
      props: { onProbe: vi.fn(), probeResults: verdict({ message: '403', ok: false, reachable: true }) }
    })
    expect(screen.getByText(new RegExp(t('testRejected')))).toBeTruthy()
  })

  it('连不上服务与密钥错分开报', () => {
    renderField({
      info: { probe: true },
      props: { onProbe: vi.fn(), probeResults: verdict({ message: 'ECONNREFUSED', reachable: false }) }
    })
    expect(screen.getByText(new RegExp(t('testUnreachable')))).toBeTruthy()
  })

  it('已存未编辑的行也留着上一次的判定', () => {
    renderField({
      info: { probe: true },
      props: { edits: {}, onProbe: vi.fn(), probeResults: verdict({ message: 'ok', ok: true }) }
    })
    expect(screen.getByText(new RegExp(t('testOk')))).toBeTruthy()
  })
})

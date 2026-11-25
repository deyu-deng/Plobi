// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { close: '关闭' },
      console: { chiefSecretary: '总秘书' },
      surface: { agentDossier: '档案', pendingTickets: '待放行' },
      // 右栏那一列现在有四条记录（写脑待批那一条 11-16 21:4x 拍定搬进对话卡），
      // 那枚按钮要读这一节的标题 ⇒ mock 里也得有这一节。
      mindPending: { title: '写脑待批' }
    }
  })
}))

import { chatTopBarRailEntries } from './chat-top-bar'
import { DetailPeek, RailChip, RailEmpty, RailRow, RailSection } from './rail-kit'
import { chatCardFaceDefsFor, createSlotRegistry } from './slot-registry'

/**
 * 那一列现在**从 slot 声明表派生**（裁定 239 第四刀）⇒ 调用点多一个 `faces` 参数。
 * 这里喂的是**出厂那份声明**算出来的那几枚（别线的判据一个字没动）。
 */
const FACES = chatCardFaceDefsFor(createSlotRegistry(), {
  common: { close: '关闭' },
  console: { chiefSecretary: '总秘书' },
  surface: { agentDossier: '档案', pendingTickets: '待放行' },
  mindPending: { title: '写脑待批' }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('rail-kit —— 对话卡右上角那一列共用的一套壳', () => {
  it('一段：小标题 ＋ 内容槽', () => {
    render(
      <RailSection count={3} title="进度">
        <span>内容</span>
      </RailSection>
    )

    expect(screen.getByText('进度')).toBeTruthy()
    expect(screen.getByText('3')).toBeTruthy()
    expect(screen.getByText('内容')).toBeTruthy()
  })

  it('一行：真的是 button，且带 row-button 那把尺', () => {
    render(<RailRow>一行</RailRow>)

    const row = screen.getByText('一行')

    expect(row.tagName).toBe('BUTTON')
    expect(row.getAttribute('type')).toBe('button')
    expect(row.closest('[data-slot="row-button"]')).toBeTruthy()
  })

  it('一颗可选中的小圆片', () => {
    render(<RailChip active>今天</RailChip>)

    const chip = screen.getByText('今天')

    expect(chip.getAttribute('aria-pressed')).toBe('true')
  })

  it('段内空态：一行破折号（10-27 屏上纪律：不写安慰话）', () => {
    render(<RailEmpty />)

    expect(screen.getByText('—')).toBeTruthy()
  })

  it('详情浮层：hover 触发器才出', () => {
    render(
      <DetailPeek content={<span>详情内容</span>}>
        <button type="button">触发</button>
      </DetailPeek>
    )

    expect(screen.queryByText('详情内容')).toBeNull()

    fireEvent.mouseEnter(screen.getByText('触发'))

    expect(screen.getByText('详情内容')).toBeTruthy()
  })

  it('目标与阶段那两处详情，走的是同一个浮层壳（data-slot 一样）', () => {
    const { container } = render(
      <>
        <DetailPeek content={<span>目标详情</span>}>
          <button type="button">目标</button>
        </DetailPeek>
        <DetailPeek content={<span>阶段详情</span>} placement="top-center">
          <button type="button">阶段</button>
        </DetailPeek>
      </>
    )

    fireEvent.mouseEnter(screen.getByText('目标'))
    fireEvent.mouseEnter(screen.getByText('阶段'))

    const peeks = container.querySelectorAll('[data-slot="detail-peek"]')

    expect(peeks.length).toBe(2)
    expect(peeks[0].getAttribute('data-slot')).toBe(peeks[1].getAttribute('data-slot'))
  })

  it('触发器原有的 onMouseEnter 不被顶掉', () => {
    const seen = vi.fn()

    render(
      <DetailPeek content={<span>详情</span>}>
        <button onMouseEnter={seen} type="button">
          触发
        </button>
      </DetailPeek>
    )

    fireEvent.mouseEnter(screen.getByText('触发'))

    expect(seen).toHaveBeenCalled()
  })
})

describe('右上角那一列是注册表驱动的', () => {
  it('注册表列出现有哪几项', () => {
    // 首席（L2）那屏：档案／待放行／**写脑待批**（11-16 21:4x 拍定从左栏
    // 底部角标搬进对话卡，`WP-MIND-CONSENT-EVENTS`）／贴边那几枚。
    // 指挥（L1）那屏没有档案那一枚（用户 11-19 22:3x）⇒ 那一档在
    // `chat-top-bar.test.tsx` 里。
    const entries = chatTopBarRailEntries({ edgeControls: {}, scope: { id: 'Aura', kind: 'agent' }, faces: FACES })

    expect(entries.map(entry => entry.id)).toEqual(['dossier', 'pending', 'mind-pending', 'edge'])
  })

  it('加一条记录就多一项 —— 不用改壳子', () => {
    const entries = chatTopBarRailEntries({ edgeControls: {}, scope: { id: 'Aura', kind: 'agent' }, faces: FACES })

    const withExtra = [...entries, { id: 'future', render: () => <button type="button">未来</button> }]

    render(
      <div>
        {withExtra.map(entry => (
          <span key={entry.id}>{entry.render()}</span>
        ))}
      </div>
    )

    expect(screen.getByText('未来')).toBeTruthy()
    expect(withExtra.length).toBe(entries.length + 1)
  })
})

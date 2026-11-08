import type { ReactNode } from 'react'

import { ConnectionControl } from './connection-control'
import { EntityAvatar } from './entity-avatar'
import { ListRow } from './primitives'

/**
 * 配置卡的共用外壳（用户 2026-11-08：「Providers 三个 Tab 得是一套语法……卡片全部统一成
 * Sources 那一屏的样子，最好能复用一个组件」）。
 *
 * 一张卡只有五格（`Docs/specs/settings-card-system.md` §1）：图标／名字／一行简介／右侧
 * **一颗**控件／藏在控件上的详情。三屏（Accounts / API keys / Subscriptions）都 import 它：
 *
 * - 第一格归 `EntityAvatar`（品牌字形或首字母＋品牌色底染），调用方只递字形/颜色；
 * - 右侧那一颗归 `ConnectionControl`（没配过＝加号、配过＝开关、在飞＝spinner、没有可点
 *   动作＝不画；坏了不换控件，旁边一颗红点、原因进 `errorTitle`）；
 * - 名字、简介、间距归 `ListRow`（原语保证字号与收缩，`icon` 那一格的间距也归它）。
 *
 * 行为（跳浏览器／写凭据／断开）仍归各屏；这里只统一「画成什么样」。`below` 是点开后那一层
 * （API key 的输入面板、订阅的配置抽屉都挂这里），壳不管里面是什么。
 */
export interface ConfigCardProps {
  /** 产品自己的名字（不是我们的叫法）。 */
  name: string
  /** 一行简介：「这是什么」。截断成一行，不写机制。 */
  description?: ReactNode
  /** 第一格图标的退路：名字首字母。 */
  monogram: string
  /** 品牌字形（来自各屏自己的品牌表）。没有就落 monogram。 */
  glyph?: ReactNode
  /** 品牌色：给了就 16% 底染。 */
  color?: string
  /** 官方标志 / favicon URL：给了就只画它。 */
  iconUrl?: string
  /** 右侧控件的形状，由状态决定。 */
  mode: 'configure' | 'toggle' | 'pending' | 'none'
  /** 开关是否打开——只有此刻真能用才许 true（用户 11-08 的真值不变量）。 */
  checked?: boolean
  /** 请求在飞：此刻按不动。不用于「它坏了」。 */
  busy?: boolean
  /** 坏了才红点（配过但此刻不能服务）。 */
  error?: boolean
  /** 红点的原因，只进 tooltip，不上正文。 */
  errorTitle?: string
  /** 加号那颗的可达名字。 */
  configureLabel: string
  /** 开关那颗的可达名字。 */
  toggleLabel: string
  /** 加号的稳定测试挂钩（屏上不靠它）。 */
  configureTestId?: string
  onConfigure?: () => void
  onToggle?: (next: boolean) => void
  /** 点开的那一层是否打开（尖角与 aria-expanded 由原语画）。 */
  expanded?: boolean
  /** 整行可点时的动作（键盘与指针走同一条路）。 */
  onActivate?: () => void
  /** 点开后的内容，或坏了时那一小行原因。 */
  below?: ReactNode
  /** 名字旁的一行提示（语气不进正文，由调用方决定）。 */
  hint?: ReactNode
  testId?: string
}

export function ConfigCard({
  below,
  busy = false,
  checked = false,
  color,
  configureLabel,
  configureTestId,
  description,
  error = false,
  errorTitle,
  expanded = false,
  glyph,
  hint,
  iconUrl,
  mode,
  monogram,
  name,
  onActivate,
  onConfigure,
  onToggle,
  testId,
  toggleLabel
}: ConfigCardProps) {
  return (
    <ListRow
      action={
        <ConnectionControl
          busy={busy}
          checked={checked}
          configureLabel={configureLabel}
          configureTestId={configureTestId}
          error={error}
          errorTitle={errorTitle}
          mode={mode}
          onConfigure={onConfigure}
          onToggle={onToggle}
          toggleLabel={toggleLabel}
        />
      }
      below={below}
      expanded={expanded}
      hint={hint}
      icon={(
        <EntityAvatar
          color={color}
          glyph={glyph}
          iconUrl={iconUrl}
          monogram={monogram}
          testId={testId ? `${testId}-icon` : undefined}
        />
      )}
      onActivate={onActivate}
      title={name}
      description={description}
    />
  )
}

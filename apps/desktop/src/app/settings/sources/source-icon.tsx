/**
 * 每条信源自己的图标（用户 10-19 第三轮：「不能只写名字，还要放对应的图标」）。
 *
 * **为什么是本地一张表而不是采集器包声明**：图标是**装饰**，不是功能。信源注册表里
 * 已经有三格真需要跨仓同步的声明（`category` · `setup_fields` · `builder`），那一格缺了
 * 产品就是不能用；图标缺了，退回首字母徽标仍然是一个能看的答案。所以它不进
 * `SourceSpec` —— 那会让「加一条信源不用改前端」那条验收**为了一个颜色**破掉。
 * 第三方包想自带品牌图标，随时可以把这一格提上去（那时候它就是第五格了）。
 *
 * **视觉与 `messaging/platform-icon.tsx` 那一族同形**（同一套底染比例、同样的圆角与
 * 字号、同样的退回首字母），但**不复用它的组件**：那个组件的表是**网关平台**的键
 * （`weixin` 而不是 `wechat`），把采集器的 id 塞进去会让下一个读的人问「为什么
 * `timetable` 在消息那一族的表里」。这里只共用形状，各管各的表。
 *
 * **品牌字形来自 simple-icons.org**（MIT）。钉钉是**没有**字形的那一种——品牌方要求
 * 下架，所以走品牌色底染 + 首字母，这不是将就，是那一族的既定做法（网关那边的 Slack
 * 同理）。**品牌色底染 16%**，让字形在任何底色上都读得出来，又不压过那一行本身。
 */

import { SiGooglecalendar, SiWechat } from '@icons-pack/react-simple-icons'
import type { ComponentType, SVGProps } from 'react'

import { Globe } from '@/lib/icons'
import { cn } from '@/lib/utils'

type IconKind = 'brand' | 'generic'

interface SourceIconSpec {
  Icon?: ComponentType<SVGProps<SVGSVGElement>>
  color: string
  kind: IconKind
  monogram?: string
}

/** 键是 `SourceSpec.source_id`。**查不到就退回首字母**（下面的 `SourceAvatar`）。 */
const SOURCE_ICONS: Record<string, SourceIconSpec> = {
  dingtalk: { color: '#3296FA', kind: 'brand', monogram: 'D' },
  // 教务就是一个要账密登录的 Web 系统 ⇒ 地球形。**不画"学校"**：那是一个具体学校的
  // 校徽，画在这儿等于替它认领了一个不属于它的身份（§2.1 那条平权：这一维是连接器形状）。
  timetable: { Icon: SiGooglecalendar, color: '#4285F4', kind: 'brand' },
  wechat: { Icon: SiWechat, color: '#07C160', kind: 'brand' },
  zju: { Icon: Globe, color: '#2563EB', kind: 'generic' }
}

/** 字体不够宽时用得上的后缀：名字第一个非字母数字的字符。 */
function monogramFor(displayName: string): string {
  const first = displayName.trim().charAt(0)

  return first ? first.toUpperCase() : '?'
}

export function SourceAvatar({
  className,
  displayName,
  sourceId
}: {
  className?: string
  displayName: string
  sourceId: string
}) {
  const spec = SOURCE_ICONS[sourceId]

  const base = cn(
    'inline-grid size-6 shrink-0 place-items-center rounded-md text-[length:var(--conversation-caption-font-size)] font-medium',
    className
  )

  // 查不到 = 一个还没被这张表认领的信源（多半是第三方包）。**这不是错误**，所以不给
  // 中性底色之外再加任何提示——用户要的是"看得见"，不是"看得懂为什么"。
  if (!spec) {
    return (
      <span aria-hidden="true" className={cn(base, 'bg-(--ui-bg-tertiary) text-(--ui-text-tertiary)')}>
        {monogramFor(displayName)}
      </span>
    )
  }

  const { Icon, color } = spec

  return (
    <span
      aria-hidden="true"
      className={base}
      style={{
        backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)`,
        color
      }}
    >
      {Icon ? <Icon className="size-3.5" /> : spec.monogram ?? monogramFor(displayName)}
    </span>
  )
}

/** 这一条有没有自己的图标。测试用它断言"第三方包走的是首字母那一支"。 */
export function hasSourceIcon(sourceId: string): boolean {
  return Object.hasOwn(SOURCE_ICONS, sourceId)
}
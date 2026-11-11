/**
 * 订阅软件卡片上的那颗图标（用户 10-27：「一个卡片显示对应的图标和简介」）。
 *
 * 三级来源，从高到低（用户 11-03：「感觉你用的是伪图标，直接用真实的图标」）：
 * ① 官方标志文件（`brand/*.svg`，各家自己站点上那枚 favicon 级 SVG，出处写在下面）；
 * ② simple-icons 里的品牌字形（`SiClaudecode` 那一族）；
 * ③ 两样都没有 ⇒ 品牌色底染 + 名字首字母。Codex 那一枚停在③：openai.com 与
 *    developers.openai.com 在这台都回 403，抓不到官方标志——宁可留首字母，也不画一个
 *    假 logo 替它认领身份。
 *
 * 表在这里，不在 hub：图标是装饰，缺了不影响那条腿能不能用。
 */

// 逐个字形深引，不走包的 barrel：`@icons-pack/react-simple-icons` 的 index 重导出约
// 三千个模块，冷启动时整片进转换图。2026-10-27 在这台实测过一次——`providers-settings`
// 的测试档一旦把 barrel 拉进图里，第一个用例光等模块解析就超过 5 s 超时线。
import SiClaudecode from '@icons-pack/react-simple-icons/icons/SiClaudecode'
import SiCline from '@icons-pack/react-simple-icons/icons/SiCline'
import SiCodebuddy from '@icons-pack/react-simple-icons/icons/SiCodebuddy'
import SiCursor from '@icons-pack/react-simple-icons/icons/SiCursor'
import SiGithubcopilot from '@icons-pack/react-simple-icons/icons/SiGithubcopilot'
import SiGooglegemini from '@icons-pack/react-simple-icons/icons/SiGooglegemini'
import SiMinimax from '@icons-pack/react-simple-icons/icons/SiMinimax'
import SiOpencode from '@icons-pack/react-simple-icons/icons/SiOpencode'
import SiQwen from '@icons-pack/react-simple-icons/icons/SiQwen'
import SiTrae from '@icons-pack/react-simple-icons/icons/SiTrae'
import SiXiaomi from '@icons-pack/react-simple-icons/icons/SiXiaomi'
import type { ComponentType, SVGProps } from 'react'

import antigravityMark from './brand/antigravity.svg'
import devinMark from './brand/devin.svg'
import kiroMark from './brand/kiro.svg'
import qoderMark from './brand/qoder.svg'
import zaiMark from './brand/zai.svg'
import { EntityAvatar } from './entity-avatar'

/** 官方标志的出处（2026-11-03 抓）。换素材时连这条一起换，别让它变成来路不明的文件。 */
export const BRAND_MARK_SOURCES: Record<string, string> = {
  'brand/antigravity.svg': 'https://antigravity.google/favicon.svg',
  'brand/devin.svg': 'https://devin.ai/favicon.svg',
  'brand/kiro.svg': 'https://kiro.dev/icon.svg',
  'brand/qoder.svg': 'https://qoder.com/favIcon.svg',
  'brand/zai.svg': 'https://z-cdn.chatglm.cn/z-ai/static/logo.svg'
}

interface QuotaIconSpec {
  // `title` 是 simple-icons 那一族自己加的无障碍属性（SVG 规范里没有 `<svg title>`），
  // 所以类型要写全，不能只当 `SVGProps`。
  Icon?: ComponentType<SVGProps<SVGSVGElement> & { title?: string }>
  /** 官方标志文件的 URL：优先级高于 `Icon`。 */
  mark?: string
  color: string
}

/** 键 = hub 的腿 id（`/plobi/auth` 的 id）。查不到 ⇒ 中性底 + 名字首字母。
 *
 * 2026-11-09 全量迁移之后，额度栏那些卡的腿 id ＝ magpie 的 provider id
 * （`cline_cli` → `cline`、`kiro_cli` → `kiro`、`antigravity_cli` → `agy-cli`、
 * `opencode_free` → `opencode-zen-free`）—— 这张表是**按腿 id** 查的，键不跟过去，
 * 品牌字形就当场消失（用户 11-10：「图标怎么没有加」）。所以：**腿 id 换了，这张表
 * 也要跟着换键**；旧键留着（那些腿退役了，还可能回来）。
 *
 * 键取自 simple-icons 自带的品牌色（`defaultColor`），不是我们编的。
 */
const QUOTA_ICONS: Record<string, QuotaIconSpec> = {
  'agy-cli': { color: '#4285F4', mark: antigravityMark },
  antigravity: { color: '#4285F4', mark: antigravityMark },
  claude_code_cli: { Icon: SiClaudecode, color: '#D97757' },
  cline: { Icon: SiCline, color: '#171412' },
  cline_cli: { Icon: SiCline, color: '#171412' },
  codex_cli: { color: '#10A37F' },
  cursor: { Icon: SiCursor, color: '#7B61FF' },
  devin: { color: '#000000', mark: devinMark },
  gemini_api: { Icon: SiGooglegemini, color: '#4285F4' },
  'github-copilot': { Icon: SiGithubcopilot, color: '#000000' },
  kiro: { color: '#FF9900', mark: kiroMark },
  kiro_cli: { color: '#FF9900', mark: kiroMark },
  'mimo-app': { Icon: SiXiaomi, color: '#FF6900' },
  'minimax-code': { Icon: SiMinimax, color: '#E73562' },
  'minimax-code-global': { Icon: SiMinimax, color: '#E73562' },
  opencode_api: { Icon: SiOpencode, color: '#1B1D21' },
  'opencode-zen-free': { Icon: SiOpencode, color: '#1B1D21' },
  opencode_free: { Icon: SiOpencode, color: '#1B1D21' },
  qoder: { color: '#615CED', mark: qoderMark },
  'qoder-cn': { color: '#615CED', mark: qoderMark },
  'qwenwork': { Icon: SiQwen, color: '#6950EF' },
  'qwenwork-enterprise': { Icon: SiQwen, color: '#6950EF' },
  'trae-cn': { Icon: SiTrae, color: '#32F08C' },
  'trae-global': { Icon: SiTrae, color: '#32F08C' },
  // WorkBuddy 停在②：11-11 试过 www.workbuddy.cn / www.workbuddy.ai 的
  // `favicon.svg` `favicon.ico` `favicon-32x32.png` `apple-touch-icon.png` `logo.svg`，
  // 六个地址全 404（站点根路由一并不存在，返回的是路由器的 JSON 404），
  // 拿不到官方标志文件。它是和 CodeBuddy 同一个账号体系的姊妹产品，
  // 暂时借 CodeBuddy 的字形与品牌蓝——不自己画一枚假 logo 替它认领身份。
  workbuddy: { Icon: SiCodebuddy, color: '#0052D9' },
  'workbuddy-ai': { Icon: SiCodebuddy, color: '#0052D9' },
  workbuddy_api: { Icon: SiCodebuddy, color: '#0052D9' },
  zcode: { color: '#126EF6', mark: zaiMark }
}

export function QuotaAppIcon({ className, id, name }: { className?: string; id: string; name: string }) {
  // 壳归 `EntityAvatar`（11-07 统一令）；这张订阅品牌表仍归订阅自己。
  const spec = QUOTA_ICONS[id]
  const monogram = (name || id || '?').trim().charAt(0).toUpperCase()
  const Icon = spec?.Icon

  return (
    <EntityAvatar
      className={className}
      color={spec?.color}
      glyph={Icon
        ? (
            // `title=""`：字形是装饰（整颗 aria-hidden），而 simple-icons 无条件渲染一个
            // `<title>`，默认值是品牌名——那会在屏上多出第二个"CodeBuddy"，读屏也念两遍。
            <Icon aria-hidden className="size-4" color={spec?.color} title="" />
          )
        : undefined}
      iconUrl={spec?.mark}
      monogram={monogram}
    />
  )
}

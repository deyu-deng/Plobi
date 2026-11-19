/**
 * 首次安装那几步（WP-ONBOARDING-WELCOME-FLOW）。
 *
 * **颜色**：用 Plobi 自己的色板（`--ui-*`），**明暗跟随系统**——不要照抄别的产品那套
 * 黑底（主人 11-18 20:4x：「引导界面太黑了。看不清。不应该 UI 颜色先用默认的 Plobi 值吗？
 * 明暗先跟随系统」）。形状仍取 Antigravity 欢迎向导的骨架：**一步一屏、居中一列、
 * 图标 ＋ 一句标题 ＋ 一段话 ＋ 至多一个主按钮**，0.4s 淡入上移 12px、三点呼吸 loader。
 *
 * **步序**（主人 11-17 20:0x 原话 ＋ 11-18 20:4x 的更正）：
 * 欢迎动效 → 界面 → 语言 → 外观（主题 ＋ 明暗）→ **三张卡（就是模型配置那一屏）** →
 * 画像 → 引导问答 → 给 Conductor 命名 → 问用户期望的称呼 → 完成。
 *
 * **三张卡里列的是「可配置的」，不是「已配置的」**（主人 11-18 20:4x：「你的卡片显示的
 * 不是可配置的，而是已经配置好的。用户如果一个没配置，那不是什么都不显示吗？这不是
 * egg and chicken 的问题吗？」）⇒ 三份都取**目录**：
 *   账号 ⇒ OAuth 目录；密钥 ⇒ 后端 model options 里的 api_key 那批；
 *   订阅额度 ⇒ hub 的名册（`authCatalog`）——**与配没配过无关**。
 *
 * **三张卡的名字直接读设置页那三栏**（`t.settings.nav.provider*`）⇒ 两处不许各说各话。
 *
 * 末步的答案落**两份文件**（主人 11-18 13:5x）：关于**用户的**（画像 ＋ 期望的称呼）⇒
 * `USER.md`；关于**Agent 自己**的（名字 ＋ 性格）⇒ 顶层 `SOUL.md`。见
 * `plobi_cli/onboarding_profile.py`。
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { ConfigCard } from '@/app/settings/config-card'
import { ProviderBrand } from '@/app/settings/provider-brand'
import { QuotaAppIcon } from '@/app/settings/quota-app-icon'
import { buildLoginCards, cardDetail, isConfigured, type LoginCard } from '@/app/settings/quota-login-cards'
import { SubscriptionSetupDialog } from '@/app/settings/subscription-setup'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { type Locale, LOCALE_META, useI18n } from '@/i18n'
import { ChevronRight } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { getGlobalModelOptions, listOAuthProviders, setOnboardingProfile } from '@/plobi'
import { useTheme } from '@/themes/context'

import {
  type OnboardingContext,
  setOnboardingMode,
  startProviderOAuth
} from '../../store/onboarding'

import { providerTitle } from './providers'

/** 那几步的步序（判据盯的就是这一串）。 */
export const FIRST_RUN_STEPS = [
  'welcome',
  'interface',
  'language',
  'appearance',
  'cards',
  'profile',
  'agentName',
  'userTitle',
  'done'
] as const

export type FirstRunStep = (typeof FIRST_RUN_STEPS)[number]

/** 三张卡里那一栏（点开一张卡之后的那一屏）。 */
export type ProviderLane = 'accounts' | 'keys' | 'desktop'

export interface FirstRunFlowProps {
  ctx: OnboardingContext
  /** 末步落完两份文件之后。 */
  onCompleted?: () => void
  /** 从哪儿起：`intro` = 欢迎那几屏；`profile` = 配完模型之后回来（画像那几屏）。 */
  startAt?: 'intro' | 'profile'
  /** 密钥那一栏的 `＋` ⇒ 交给既有的 key 表单那一屏（那边是真正的输入面板）。 */
  onConfigureKey?: () => void
}

interface Answers {
  purpose: string
  domain: string
  user_title: string
  agent_name: string
  agent_personality: string
}

const EMPTY_ANSWERS: Answers = {
  purpose: '',
  domain: '',
  user_title: '',
  agent_name: '',
  agent_personality: ''
}

type Copy = ReturnType<typeof useI18n>['t']['onboarding']['firstRun']

export function FirstRunFlow({
  ctx,
  onCompleted,
  onConfigureKey,
  startAt = 'intro'
}: FirstRunFlowProps) {
  const { t } = useI18n()
  const { mode, setMode, availableThemes, setTheme, themeName } = useTheme()
  const [step, setStep] = useState<FirstRunStep>(startAt === 'profile' ? 'profile' : 'welcome')
  // 「三张卡」里点开的那一栏（null ＝ 还在三张卡那屏）。**状态在这一层**——
  // 底下那两颗按钮也得知道：列表屏的「返回」该回三张卡，不是退到上一步。
  const [lane, setLane] = useState<ProviderLane | null>(null)
  const [answers, setAnswers] = useState<Answers>(EMPTY_ANSWERS)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const copy = t.onboarding.firstRun

  // 欢迎那一屏自己走完（图标淡入 → 三点呼吸 → 下一句）。
  useEffect(() => {
    if (step !== 'welcome') {
      return
    }

    const timer = window.setTimeout(() => setStep('interface'), 1400)

    return () => window.clearTimeout(timer)
  }, [step])

  const go = (next: FirstRunStep) => {
    setError('')
    setStep(next)
  }

  const finish = async () => {
    setSaving(true)
    setError('')

    try {
      await setOnboardingProfile(answers)
      onCompleted?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  // 「挑一家模型」那一步单独占一列：**46rem 宽**（≥ `--container-2xl` 的 42rem ⇒
  // 设置页那张卡靠右那颗控件才排得进同一行）＋ **固定高度**（主人 11-19 13:2x：「模型配置
  // 那两个按钮也要放到原来的位置去呀。和其他界面的高度一致」）——别的屏是「居中的一整块」，
  // 这一屏给一个**同样高度**的整块：三张卡那屏的内容居中、点开一栏的列表屏标题在上、
  // 列表占满其余（内部滚动）⇒ 两处的按钮都落在与其他屏相同的高度上。
  const wideStep = step === 'cards'

  return (
    <div className="fixed inset-0 z-1300 flex items-center justify-center bg-[color-mix(in_srgb,var(--ui-chat-surface-background)_78%,transparent)] backdrop-blur-[1.125rem] backdrop-saturate-[1.08] p-6">
      <style>{FIRST_RUN_CSS}</style>
      <div
        className={cn(
          'flex w-full flex-col items-center',
          wideStep ? 'h-[27rem] max-w-[46rem]' : 'max-w-[30rem]'
        )}
      >
        <div
          className={cn(
            'flex w-full flex-col items-center',
            wideStep ? 'min-h-0 flex-1' : 'min-h-[22rem]'
          )}
          key={step}
        >
          {step === 'welcome' ? <WelcomeStep copy={copy} /> : null}
          {step === 'interface' ? <InterfaceStep copy={copy} /> : null}
          {step === 'language' ? <LanguageStep copy={copy} /> : null}
          {step === 'appearance' ? (
            <AppearanceStep
              copy={copy}
              mode={mode}
              onMode={setMode}
              onTheme={setTheme}
              themeName={themeName}
              themes={availableThemes}
            />
          ) : null}
          {step === 'cards' ? (
            <CardsStep copy={copy} ctx={ctx} lane={lane} onConfigureKey={onConfigureKey} onLaneChange={setLane} />
          ) : null}
          {step === 'profile' ? (
            <ProfileStep answers={answers} copy={copy} onChange={patch => setAnswers(a => ({ ...a, ...patch }))} />
          ) : null}
          {step === 'agentName' ? (
            <AgentNameStep
              answers={answers}
              copy={copy}
              onChange={patch => setAnswers(a => ({ ...a, ...patch }))}
            />
          ) : null}
          {step === 'userTitle' ? (
            <UserTitleStep
              answers={answers}
              copy={copy}
              onChange={patch => setAnswers(a => ({ ...a, ...patch }))}
            />
          ) : null}
          {step === 'done' ? <DoneStep copy={copy} /> : null}
        </div>

        {error ? <p className="mt-4 text-center text-xs text-destructive">{error}</p> : null}

        <StepFooter
          copy={copy}
          onBack={() => {
            // 点开的那一栏 ⇒ 先回三张卡那屏（不是退到上一步）。
            if (step === 'cards' && lane) {
              setLane(null)

              return
            }

            const index = FIRST_RUN_STEPS.indexOf(step)

            if (index > 0) {
              setError('')
              setStep(FIRST_RUN_STEPS[index - 1])
            }
          }}
          onNext={() => {
            const index = FIRST_RUN_STEPS.indexOf(step)

            if (step === 'profile') {
              go('agentName')

              return
            }

            if (step === 'agentName') {
              go('userTitle')

              return
            }

            if (step === 'userTitle') {
              go('done')

              return
            }

            if (step === 'done') {
              void finish()

              return
            }

            go(FIRST_RUN_STEPS[index + 1])
          }}
          onSkip={() => {
            if (step === 'profile' || step === 'agentName' || step === 'userTitle') {
              go('done')
            }
          }}
          saving={saving}
          step={step}
        />
      </div>
    </div>
  )
}

/** 一步一屏：图标 ＋ 标题 ＋ 一段话 ＋ 至多一个主按钮。 */
function Screen({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('fr-screen', className)}>{children}</div>
}

/**
 * 每屏那块**玻璃面**：与那三张卡同一套材质（半透 ＋ 模糊 ＋ 主题色渐变 ＋ 发丝边）。
 * 标题与一句说明仍是素文字——玻璃只托内容（主人 11-19 09:2x：「其他引导页的气质也统一
 * 一下」）。
 */
function Panel({ children }: { children: React.ReactNode }) {
  return <div className="fr-panel">{children}</div>
}

function Title({ children }: { children: React.ReactNode }) {
  return <h1 className="fr-title">{children}</h1>
}

function Desc({ children }: { children: React.ReactNode }) {
  return <p className="fr-desc">{children}</p>
}

function WelcomeStep({ copy }: { copy: Copy }) {
  return (
    <Screen>
      <div className="fr-icon">
        <svg aria-hidden="true" fill="none" height="36" stroke="currentColor" strokeWidth="1.4" viewBox="0 0 24 24">
          <path d="M12 2.6 20.4 7v10L12 21.4 3.6 17V7L12 2.6Z" />
          <path d="M12 7.2 16.8 9.6v4.8L12 16.8 7.2 14.4V9.6L12 7.2Z" />
        </svg>
      </div>
      <div className="fr-loader">
        <i />
        <i />
        <i />
      </div>
      <Title>{copy.welcomeTitle}</Title>
      <Desc>{copy.welcomeDesc}</Desc>
    </Screen>
  )
}

function InterfaceStep({ copy }: { copy: Copy }) {
  return (
    <Screen>
      <div className="fr-icon">
        <svg aria-hidden="true" fill="none" height="36" stroke="currentColor" strokeWidth="1.4" viewBox="0 0 24 24">
          <rect height="13" rx="2" width="19" x="2.5" y="4" />
          <path d="M8.5 20.5h7" />
        </svg>
      </div>
      <Title>{copy.interfaceTitle}</Title>
      <Desc>{copy.interfaceDesc}</Desc>
    </Screen>
  )
}

function LanguageStep({ copy }: { copy: Copy }) {
  const { locale, setLocale } = useI18n()
  const locales = Object.entries(LOCALE_META) as Array<[Locale, (typeof LOCALE_META)[Locale]]>

  return (
    <Screen>
      <Title>{copy.languageTitle}</Title>
      <Desc>{copy.languageDesc}</Desc>
      <Panel>
        <div className="fr-list">
          {locales.map(([code, meta]) => (
            <button
              aria-pressed={code === locale}
              className="fr-item"
              key={code}
              onClick={() => {
                void setLocale(code)
              }}
              type="button"
            >
              <span>{meta.name}</span>
            </button>
          ))}
        </div>
      </Panel>
    </Screen>
  )
}

/** 外观：主题 ＋ 明暗（设置页「外观」那一栏就是这两样）。 */
function AppearanceStep({
  copy,
  mode,
  onMode,
  onTheme,
  themeName,
  themes
}: {
  copy: Copy
  mode: 'light' | 'dark' | 'system'
  onMode: (mode: 'light' | 'dark' | 'system') => void
  onTheme: (name: string) => void
  themeName: string
  themes: { name: string; label: string }[]
}) {
  return (
    <Screen>
      <Title>{copy.appearanceTitle}</Title>
      <Desc>{copy.appearanceDesc}</Desc>
      <Panel>
        <div className="fr-group">
        <span className="fr-label">{copy.modeLabel}</span>
        <SegmentedControl
          onChange={onMode}
          options={[
            { id: 'light', label: copy.themeLight },
            { id: 'dark', label: copy.themeDark },
            { id: 'system', label: copy.themeSystem }
          ]}
          value={mode}
        />
      </div>
      <div className="fr-group">
          <span className="fr-label">{copy.themeLabel}</span>
          <div className="fr-themes">
            {themes.map(theme => (
              <button
                aria-pressed={theme.name === themeName}
                className="fr-theme"
                key={theme.name}
                onClick={() => onTheme(theme.name)}
                type="button"
              >
                {theme.label}
              </button>
            ))}
          </div>
        </div>
      </Panel>
    </Screen>
  )
}

/**
 * 三张卡 ＝ **模型配置那一屏**（主人 11-18 20:4x：「模型配置界面放三张卡片」）。
 *
 * 每张卡里列的是**目录**（可配置的那些），不是「已经配好的」：
 *   账号 ⇒ OAuth 目录；密钥 ⇒ 后端 model options 里的 api_key 那批；
 *   订阅额度 ⇒ hub 的名册。三份都与配没配过无关 ⇒ 一个都没配时照样列得出来。
 */
function CardsStep({
  copy,
  ctx,
  lane,
  onConfigureKey,
  onLaneChange
}: {
  copy: Copy
  ctx: OnboardingContext
  lane: ProviderLane | null
  onConfigureKey?: () => void
  onLaneChange: (lane: ProviderLane | null) => void
}) {
  const { t } = useI18n()
  const setLane = onLaneChange

  const { data: oauth } = useQuery({
    queryKey: ['first-run-oauth-providers'],
    queryFn: async () => (await listOAuthProviders()).providers ?? [],
    staleTime: 5 * 60 * 1000
  })

  const { data: keyProviders } = useQuery({
    queryKey: ['first-run-key-catalog'],
    queryFn: async () => {
      const res = await getGlobalModelOptions({ includeUnconfigured: true, explicitOnly: false })

      return (res?.providers ?? []).filter(
        p => (!p.auth_type || p.auth_type === 'api_key') && Boolean(p.key_env)
      )
    },
    staleTime: 5 * 60 * 1000
  })

  const [setupCard, setSetupCard] = useState<LoginCard | null>(null)

  const { data: roster, refetch: refetchRoster } = useQuery({
    queryKey: ['first-run-auth-catalog'],
    queryFn: async () => {
      const rows = await window.plobiDesktop?.authCatalog?.()

      return rows ?? []
    },
    staleTime: 5 * 60 * 1000
  })

  const quotaCards = useMemo(() => buildLoginCards(roster ?? []), [roster])

  const lanes = [
    {
      count: (oauth ?? []).length,
      desc: copy.cardsDescAccounts,
      id: 'accounts' as const,
      label: t.settings.nav.providerAccounts,
      // 左下角那排＝**设置页同一栏里那几家人的品牌矢量图标**（同一个品牌表、同一枚
      // 首字母底染）：首装与设置页从此看到同一批标志，不是两套画法。
      marks: (oauth ?? []).slice(0, 4).map(p => (
        <ProviderBrand key={p.id} name={providerTitle(p)} slug={p.id} />
      ))
    },
    {
      count: (keyProviders ?? []).length,
      desc: copy.cardsDescKeys,
      id: 'keys' as const,
      label: t.settings.nav.providerApiKeys,
      marks: (keyProviders ?? []).slice(0, 4).map(p => (
        <ProviderBrand envKey={p.key_env} key={p.slug} name={p.name} slug={p.slug} />
      ))
    },
    {
      count: (roster ?? []).length,
      desc: copy.cardsDescSubscriptions,
      id: 'desktop' as const,
      label: t.settings.nav.providerSubscriptions,
      marks: quotaCards.slice(0, 4).map(card => (
        <QuotaAppIcon id={card.id} key={card.id} name={card.name} />
      ))
    }
  ]

  // 第一屏：左中右三张大卡片（主人 11-19 08:4x：「卡片高度加一点，底部应该靠近下面
  // 两个按钮……下面左下角有设置里里面对应的矢量图标……卡片有主题色的渐变效果，
  // 符合主界面底部的玻璃渐变磨砂气质」）。
  if (!lane) {
    return (
      <Screen className="fr-fill fr-cards">
        <div className="fr-cards-head">
          <Title>{copy.cardsTitle}</Title>
        </div>
        <div className="fr-lanes">
          {lanes.map(l => (
            <button className="fr-lane" key={l.id} onClick={() => setLane(l.id)} type="button">
              <span className="fr-lane-body">
                <span className="fr-lane-top">
                  <span className="fr-lane-name">{l.label}</span>
                  <span className="fr-lane-count">{copy.cardsCount.replace('{n}', String(l.count))}</span>
                </span>
                <span className="fr-lane-desc">{l.desc}</span>
                <span className="fr-lane-foot">
                  <span className="fr-lane-marks">
                    {l.marks}
                    {l.count > l.marks.length ? (
                      <span className="fr-lane-more">+{l.count - l.marks.length}</span>
                    ) : null}
                  </span>
                  <ChevronRight className="fr-lane-go" />
                </span>
              </span>
            </button>
          ))}
        </div>
      </Screen>
    )
  }

  // 第二屏：那一栏的供应商，**滚动列表**，每张卡就是设置页那一张（`ConfigCard`）。
  const current = lanes.find(l => l.id === lane)

  return (
    <Screen className="fr-fill fr-lane-screen">
      <Title>{current?.label}</Title>
      <div className="fr-list-scroll">
        {lane === 'accounts'
          ? (oauth ?? []).map(p => (
              <ConfigCard
                avatar={<ProviderBrand name={providerTitle(p)} slug={p.id} />}
                configureLabel={copy.configureLabel.replace('{name}', providerTitle(p))}
                description={t.onboarding.flowSubtitles[p.flow]}
                key={p.id}
                mode="configure"
                monogram={providerTitle(p).trim().slice(0, 1).toUpperCase()}
                name={providerTitle(p)}
                onConfigure={() => void startProviderOAuth(p, ctx)}
                toggleLabel={copy.toggleLabel.replace('{name}', providerTitle(p))}
              />
            ))
          : null}
        {lane === 'keys'
          ? (keyProviders ?? []).map(p => (
              <ConfigCard
                avatar={<ProviderBrand envKey={p.key_env} name={p.name} slug={p.slug} />}
                configureLabel={copy.configureLabel.replace('{name}', p.name)}
                key={p.slug}
                mode="configure"
                monogram={p.name.trim().slice(0, 1).toUpperCase()}
                name={p.name}
                onConfigure={() => {
                  setOnboardingMode('apikey')
                  onConfigureKey?.()
                }}
                toggleLabel={copy.toggleLabel.replace('{name}', p.name)}
              />
            ))
          : null}
        {lane === 'desktop'
          ? quotaCards.map(card => (
              <ConfigCard
                avatar={<QuotaAppIcon id={card.id} name={card.name} />}
                configureLabel={copy.configureLabel.replace('{name}', card.name)}
                description={cardDetail(card)}
                key={card.id}
                mode={isConfigured(card.state) ? 'toggle' : 'configure'}
                monogram={card.name.trim().slice(0, 1).toUpperCase()}
                name={card.name}
                onConfigure={() => setSetupCard(card)}
                toggleLabel={copy.toggleLabel.replace('{name}', card.name)}
              />
            ))
          : null}
      </div>
      {/* hub 那条腿的登录抽屉：设置页那一个（同一个组件），`onLogin` 直调网关的 auth。 */}
      {setupCard ? (
        <SubscriptionSetupDialog
          card={setupCard}
          onChanged={() => {
            void refetchRoster()
          }}
          onClose={() => setSetupCard(null)}
          onLogin={async (appId: string) => {
            const gw = window.plobiDesktop?.plobiGateway

            if (!gw) {
              return { ok: false }
            }

            const res = await gw.auth(appId)

            return { ok: Boolean(res?.ok) }
          }}
          open
        />
      ) : null}
    </Screen>
  )
}

function ProfileStep({
  copy,
  answers,
  onChange
}: {
  copy: Copy
  answers: Answers
  onChange: (patch: Partial<Answers>) => void
}) {
  return (
    <Screen>
      <Title>{copy.profileTitle}</Title>
      <Desc>{copy.profileDesc}</Desc>
      <Panel>
        {/* 两题改成**选项卡**（Qoder 的首装那一路：「哪些角色最符合你的工作？」＝一排
            可选的芯片，单选）——不用空框让人自己想词。 */}
        <div className="fr-group">
          <span className="fr-label">{copy.profilePurpose}</span>
          <div className="fr-chips">
            {copy.profilePurposes.map(option => (
              <button
                aria-pressed={answers.purpose === option.label}
                className="fr-chip"
                key={option.id}
                onClick={() => onChange({ purpose: option.label })}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="fr-group">
          <span className="fr-label">{copy.profileDomain}</span>
          <div className="fr-chips">
            {copy.profileDomains.map(option => (
              <button
                aria-pressed={answers.domain === option.label}
                className="fr-chip"
                key={option.id}
                onClick={() => onChange({ domain: option.label })}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </Panel>
    </Screen>
  )
}

function AgentNameStep({
  copy,
  answers,
  onChange
}: {
  copy: Copy
  answers: Answers
  onChange: (patch: Partial<Answers>) => void
}) {
  return (
    <Screen>
      <Title>{copy.agentNameTitle}</Title>
      <Desc>{copy.agentNameDesc}</Desc>
      <Panel>
        <label className="fr-label" htmlFor="fr-agent-name">
          {copy.agentNameTitle}
        </label>
        <Input
          id="fr-agent-name"
          onChange={e => onChange({ agent_name: e.target.value })}
          placeholder={copy.agentNamePlaceholder}
          value={answers.agent_name}
        />
        <label className="fr-label" htmlFor="fr-agent-personality">
          {copy.agentNamePersonality}
        </label>
        <Input
          id="fr-agent-personality"
          onChange={e => onChange({ agent_personality: e.target.value })}
          placeholder={copy.agentNamePersonalityPlaceholder}
          value={answers.agent_personality}
        />
      </Panel>
    </Screen>
  )
}

function UserTitleStep({
  copy,
  answers,
  onChange
}: {
  copy: Copy
  answers: Answers
  onChange: (patch: Partial<Answers>) => void
}) {
  return (
    <Screen>
      <Title>{copy.userTitleTitle}</Title>
      <Desc>{copy.userTitleDesc}</Desc>
      <Panel>
        <label className="fr-label" htmlFor="fr-user-title">
          {copy.userTitleTitle}
        </label>
        <Input
          id="fr-user-title"
          onChange={e => onChange({ user_title: e.target.value })}
          placeholder={copy.userTitlePlaceholder}
          value={answers.user_title}
        />
      </Panel>
    </Screen>
  )
}

function DoneStep({ copy }: { copy: Copy }) {
  return (
    <Screen>
      <div className="fr-icon">
        <svg aria-hidden="true" fill="none" height="36" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
          <path d="M4 12.5 9.5 18 20 6.5" />
        </svg>
      </div>
      <Title>{copy.doneTitle}</Title>
      <Desc>{copy.doneDesc}</Desc>
    </Screen>
  )
}

function StepFooter({
  copy,
  saving,
  step,
  onBack,
  onNext,
  onSkip
}: {
  copy: Copy
  saving: boolean
  step: FirstRunStep
  onBack: () => void
  onNext: () => void
  onSkip: () => void
}) {
  const skippable = step === 'profile' || step === 'agentName' || step === 'userTitle'
  const last = step === 'done'
  const index = FIRST_RUN_STEPS.indexOf(step)

  return (
    <div className="mt-7 flex w-full flex-col items-center gap-4">
      <div className="flex items-center gap-2">
        {index > 0 ? (
          <Button onClick={onBack} size="sm" variant="ghost">
            {copy.back}
          </Button>
        ) : null}
        <Button disabled={saving} onClick={onNext} size="sm">
          {last ? copy.finish : copy.next}
        </Button>
      </div>
      {skippable ? (
        <button className="text-xs text-(--ui-text-tertiary) hover:text-foreground" onClick={onSkip} type="button">
          {copy.skip}
        </button>
      ) : null}
      <div className="fr-dots">
        {FIRST_RUN_STEPS.map((s, i) => (
          <i className={i === index ? 'on' : i < index ? 'done' : ''} key={s} />
        ))}
      </div>
    </div>
  )
}

/** 形状：一步一屏 ＋ 0.4s 淡入上移 12px ＋ 三点呼吸 loader；颜色全走 Plobi 自己的 `--ui-*`。 */
const FIRST_RUN_CSS = `
@keyframes fr-fade-in { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
@keyframes fr-dot-pulse { 0%, 100% { opacity: 0.25; transform: scale(0.9); } 50% { opacity: 1; transform: scale(1.05); } }
.fr-screen { display: flex; width: 100%; flex-direction: column; align-items: center; text-align: center; animation: fr-fade-in 0.4s ease; }
.fr-title { font-size: 1.375rem; font-weight: 600; letter-spacing: -0.01em; margin-bottom: 0.5rem; color: var(--ui-text-primary); }
.fr-desc { font-size: 0.8125rem; line-height: 1.6; color: var(--ui-text-secondary); margin-bottom: 1.25rem; }
.fr-icon { display: flex; width: 4.5rem; height: 4.5rem; align-items: center; justify-content: center; border-radius: 1.25rem; border: 1px solid color-mix(in srgb, var(--ui-stroke-secondary) 70%, transparent); background: color-mix(in srgb, var(--dt-card) 58%, transparent); backdrop-filter: blur(0.75rem) saturate(1.12); -webkit-backdrop-filter: blur(0.75rem) saturate(1.12); box-shadow: 0 16px 36px -24px color-mix(in srgb, var(--ui-text-primary) 45%, transparent); margin-bottom: 1.25rem; color: var(--ui-text-primary); }
.fr-loader { display: flex; gap: 0.45rem; margin-bottom: 1rem; }
.fr-loader i { width: 0.45rem; height: 0.45rem; border-radius: 9999px; background: var(--ui-text-secondary); animation: fr-dot-pulse 1.1s ease-in-out infinite; }
.fr-loader i:nth-child(2) { animation-delay: 0.18s; }
.fr-loader i:nth-child(3) { animation-delay: 0.36s; }
/* 每屏那块玻璃面：与那三张卡同一套材质。 */
.fr-panel { position: relative; width: 100%; overflow: hidden; text-align: left; border-radius: 1rem; border: 1px solid color-mix(in srgb, var(--ui-stroke-secondary) 70%, transparent); background: color-mix(in srgb, var(--dt-card) 58%, transparent); backdrop-filter: blur(0.75rem) saturate(1.12); -webkit-backdrop-filter: blur(0.75rem) saturate(1.12); padding: 1rem; box-shadow: 0 18px 40px -28px color-mix(in srgb, var(--ui-text-primary) 45%, transparent); }
.fr-panel::before { content: ''; position: absolute; inset: 0; background: linear-gradient(152deg, color-mix(in srgb, var(--ui-accent) 22%, transparent), transparent 58%); pointer-events: none; }
.fr-panel > * { position: relative; z-index: 1; }
.fr-list { display: flex; width: 100%; flex-direction: column; gap: 0.375rem; }
.fr-item { width: 100%; border-radius: 0.625rem; border: 1px solid transparent; background: color-mix(in srgb, var(--dt-card) 34%, transparent); padding: 0.6875rem 0.875rem; text-align: left; font-size: 0.8125rem; color: var(--ui-text-primary); transition: background-color 0.15s ease, border-color 0.15s ease; }
.fr-item:hover { background: color-mix(in srgb, var(--dt-card) 52%, transparent); }
.fr-item[aria-pressed='true'] { border-color: color-mix(in srgb, var(--ui-accent) 55%, transparent); background: color-mix(in srgb, var(--ui-accent) 12%, transparent); }
.fr-group { width: 100%; margin-bottom: 1rem; text-align: left; }
.fr-panel .fr-group:last-child { margin-bottom: 0; }
.fr-themes { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.fr-chips { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.fr-chip { border-radius: 0.625rem; border: 1px solid color-mix(in srgb, var(--ui-stroke-secondary) 55%, transparent); background: color-mix(in srgb, var(--dt-card) 40%, transparent); backdrop-filter: blur(0.5rem) saturate(1.1); -webkit-backdrop-filter: blur(0.5rem) saturate(1.1); padding: 0.4375rem 0.75rem; font-size: 0.8125rem; color: var(--ui-text-primary); transition: background-color 0.15s ease, border-color 0.15s ease; }
.fr-chip:hover { background: color-mix(in srgb, var(--dt-card) 58%, transparent); }
.fr-chip[aria-pressed='true'] { border-color: color-mix(in srgb, var(--ui-accent) 60%, transparent); background: color-mix(in srgb, var(--ui-accent) 14%, transparent); }
.fr-theme { border-radius: 0.5rem; border: 1px solid color-mix(in srgb, var(--ui-stroke-secondary) 55%, transparent); background: color-mix(in srgb, var(--dt-card) 40%, transparent); padding: 0.375rem 0.625rem; font-size: 0.75rem; color: var(--ui-text-primary); }
.fr-theme[aria-pressed='true'] { border-color: color-mix(in srgb, var(--ui-accent) 60%, transparent); background: color-mix(in srgb, var(--ui-accent) 12%, transparent); }
/* 三张卡那一屏：标题在上、卡贴着下面那两颗按钮（主人 11-19 08:4x）。 */
.fr-fill { flex: 1; min-height: 0; }
.fr-cards { justify-content: center; }
.fr-cards-head { display: flex; width: 100%; flex: none; flex-direction: column; margin-bottom: 1.25rem; }
.fr-lanes { display: grid; width: 100%; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.75rem; }
/* 玻璃磨砂的大卡：半透底 ＋ 模糊 ＋ 主题色渐变 ＋ 左下角那团柔光。 */
.fr-lane { position: relative; display: flex; min-height: 13rem; flex-direction: column; overflow: hidden; border-radius: 1rem; border: 1px solid color-mix(in srgb, var(--ui-stroke-secondary) 72%, transparent); background: color-mix(in srgb, var(--dt-card) 58%, transparent); backdrop-filter: blur(0.75rem) saturate(1.12); -webkit-backdrop-filter: blur(0.75rem) saturate(1.12); text-align: left; color: var(--ui-text-primary); transition: transform 0.18s ease, border-color 0.18s ease; }
.fr-lane:hover { transform: translateY(-2px); border-color: color-mix(in srgb, var(--ui-accent) 45%, transparent); }
.fr-lane::before { content: ''; position: absolute; inset: 0; background: linear-gradient(152deg, color-mix(in srgb, var(--ui-accent) 26%, transparent), transparent 58%); pointer-events: none; }
.fr-lane::after { content: ''; position: absolute; left: -25%; bottom: -60%; width: 95%; height: 95%; border-radius: 9999px; background: radial-gradient(circle, color-mix(in srgb, var(--ui-accent) 30%, transparent), transparent 70%); filter: blur(14px); pointer-events: none; }
.fr-lane-body { position: relative; z-index: 1; display: flex; height: 100%; flex-direction: column; padding: 0.875rem; }
.fr-lane-top { display: flex; align-items: baseline; justify-content: space-between; gap: 0.5rem; }
.fr-lane-name { font-size: 1rem; font-weight: 600; letter-spacing: -0.01em; color: var(--ui-text-primary); }
.fr-lane-count { font-size: 0.6875rem; color: var(--ui-text-tertiary); }
.fr-lane-desc { margin-top: 0.3125rem; font-size: 0.75rem; line-height: 1.5; color: var(--ui-text-secondary); }
.fr-lane-foot { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-top: auto; padding-top: 0.75rem; }
.fr-lane-marks { display: flex; align-items: center; gap: 0.25rem; }
.fr-lane-more { margin-left: 0.125rem; font-size: 0.6875rem; color: var(--ui-text-tertiary); }
.fr-lane-go { width: 1rem; height: 1rem; color: var(--ui-text-tertiary); transition: transform 0.18s ease, color 0.18s ease; }
.fr-lane:hover .fr-lane-go { transform: translateX(2px); color: var(--ui-accent); }
.fr-lane-screen .fr-list-scroll { flex: 1; max-height: none; }
.fr-list-scroll { display: flex; width: 100%; max-height: 58vh; flex-direction: column; gap: 0.5rem; overflow-y: auto; padding-right: 0.25rem; }
.fr-label { display: block; margin-bottom: 0.375rem; font-size: 0.75rem; color: var(--ui-text-secondary); }
.fr-dots { display: flex; gap: 0.375rem; }
.fr-dots i { width: 0.3125rem; height: 0.3125rem; border-radius: 9999px; background: var(--ui-bg-tertiary); }
.fr-dots i.on { background: var(--ui-accent); }
.fr-dots i.done { background: color-mix(in srgb, var(--ui-accent) 55%, transparent); }
`

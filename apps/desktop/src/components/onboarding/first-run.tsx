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
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { type Locale, LOCALE_META, useI18n } from '@/i18n'
import { getGlobalModelOptions, listOAuthProviders, setOnboardingProfile } from '@/plobi'
import { useTheme } from '@/themes/context'

import type { OnboardingContext } from '../../store/onboarding'

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

export interface FirstRunFlowProps {
  ctx: OnboardingContext
  /** 末步落完两份文件之后。 */
  onCompleted?: () => void
  /** 从哪儿起：`intro` = 欢迎那几屏；`profile` = 配完模型之后回来（画像那几屏）。 */
  startAt?: 'intro' | 'profile'
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

export function FirstRunFlow({ ctx, onCompleted, startAt = 'intro' }: FirstRunFlowProps) {
  const { t } = useI18n()
  const { mode, setMode, availableThemes, setTheme, themeName } = useTheme()
  const [step, setStep] = useState<FirstRunStep>(startAt === 'profile' ? 'profile' : 'welcome')
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

  return (
    <div className="fixed inset-0 z-1300 flex items-center justify-center bg-(--ui-chat-surface-background) p-6">
      <style>{FIRST_RUN_CSS}</style>
      <div className="flex w-full max-w-[30rem] flex-col items-center">
        <div className="flex min-h-[22rem] w-full flex-col items-center" key={step}>
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
          {step === 'cards' ? <CardsStep copy={copy} /> : null}
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
function Screen({ children }: { children: React.ReactNode }) {
  return <div className="fr-screen">{children}</div>
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
function CardsStep({ copy }: { copy: Copy }) {
  const { t } = useI18n()
  const [open, setOpen] = useState<'accounts' | 'keys' | 'desktop'>('accounts')

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

  const { data: roster } = useQuery({
    queryKey: ['first-run-auth-catalog'],
    queryFn: async () => {
      const rows = await window.plobiDesktop?.authCatalog?.()

      return rows ?? []
    },
    staleTime: 5 * 60 * 1000
  })

  return (
    <Screen>
      <Title>{copy.cardsTitle}</Title>
      <Desc>{copy.cardsDesc}</Desc>
      <div className="fr-cards">
        <ProviderCard
          body={
            (oauth ?? []).length > 0 ? (
              (oauth ?? []).map(p => (
                <div className="fr-row" key={p.id}>
                  <span className="fr-dot" />
                  <span>{p.name}</span>
                </div>
              ))
            ) : (
              <div className="fr-row fr-muted">{t.settings.nav.providerAccounts}</div>
            )
          }
          label={t.settings.nav.providerAccounts}
          onClick={() => setOpen('accounts')}
          open={open === 'accounts'}
        />
        <ProviderCard
          body={
            (keyProviders ?? []).length > 0 ? (
              (keyProviders ?? []).map(p => (
                <div className="fr-row" key={p.slug}>
                  <span className="fr-dot" />
                  <span>{p.name}</span>
                </div>
              ))
            ) : (
              <div className="fr-row fr-muted">{t.settings.nav.providerApiKeys}</div>
            )
          }
          label={t.settings.nav.providerApiKeys}
          onClick={() => setOpen('keys')}
          open={open === 'keys'}
        />
        <ProviderCard
          body={
            (roster ?? []).length > 0 ? (
              (roster ?? []).map(row => (
                <div className="fr-row" key={row.id}>
                  <span className="fr-dot" />
                  <span>{row.id}</span>
                </div>
              ))
            ) : (
              <div className="fr-row fr-muted">{t.settings.nav.providerSubscriptions}</div>
            )
          }
          label={t.settings.nav.providerSubscriptions}
          onClick={() => setOpen('desktop')}
          open={open === 'desktop'}
        />
      </div>
    </Screen>
  )
}

function ProviderCard({
  label,
  body,
  open,
  onClick
}: {
  label: string
  body: React.ReactNode
  open: boolean
  onClick: () => void
}) {
  return (
    <div className={open ? 'fr-card open' : 'fr-card'}>
      <button className="fr-card-head" onClick={onClick} type="button">
        <span>{label}</span>
        <span className="fr-chev">›</span>
      </button>
      <div className="fr-card-body">{body}</div>
    </div>
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
      <div className="w-full">
        <label className="fr-label" htmlFor="fr-purpose">
          {copy.profilePurpose}
        </label>
        <Input
          id="fr-purpose"
          onChange={e => onChange({ purpose: e.target.value })}
          placeholder={copy.profilePurposePlaceholder}
          value={answers.purpose}
        />
        <label className="fr-label" htmlFor="fr-domain">
          {copy.profileDomain}
        </label>
        <Input
          id="fr-domain"
          onChange={e => onChange({ domain: e.target.value })}
          placeholder={copy.profileDomainPlaceholder}
          value={answers.domain}
        />
      </div>
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
      <div className="w-full">
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
      </div>
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
      <div className="w-full">
        <label className="fr-label" htmlFor="fr-user-title">
          {copy.userTitleTitle}
        </label>
        <Input
          id="fr-user-title"
          onChange={e => onChange({ user_title: e.target.value })}
          placeholder={copy.userTitlePlaceholder}
          value={answers.user_title}
        />
      </div>
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
.fr-icon { display: flex; width: 4.5rem; height: 4.5rem; align-items: center; justify-content: center; border-radius: 1.25rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-tertiary); margin-bottom: 1.25rem; color: var(--ui-text-primary); }
.fr-loader { display: flex; gap: 0.45rem; margin-bottom: 1rem; }
.fr-loader i { width: 0.45rem; height: 0.45rem; border-radius: 9999px; background: var(--ui-text-secondary); animation: fr-dot-pulse 1.1s ease-in-out infinite; }
.fr-loader i:nth-child(2) { animation-delay: 0.18s; }
.fr-loader i:nth-child(3) { animation-delay: 0.36s; }
.fr-list { display: flex; width: 100%; flex-direction: column; gap: 0.5rem; }
.fr-item { width: 100%; border-radius: 0.5rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-secondary); padding: 0.75rem 0.875rem; text-align: left; font-size: 0.8125rem; color: var(--ui-text-primary); }
.fr-item[aria-pressed='true'] { border-color: var(--ui-accent); }
.fr-group { width: 100%; margin-bottom: 1rem; text-align: left; }
.fr-themes { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.fr-theme { border-radius: 0.5rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-secondary); padding: 0.375rem 0.625rem; font-size: 0.75rem; color: var(--ui-text-primary); }
.fr-theme[aria-pressed='true'] { border-color: var(--ui-accent); }
.fr-cards { display: flex; width: 100%; flex-direction: column; gap: 0.625rem; margin-bottom: 1.25rem; }
.fr-card { border-radius: 0.75rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-secondary); overflow: hidden; text-align: left; }
.fr-card-head { display: flex; width: 100%; align-items: center; gap: 0.75rem; padding: 0.875rem 1rem; font-size: 0.8125rem; font-weight: 500; color: var(--ui-text-primary); }
.fr-chev { margin-left: auto; color: var(--ui-text-tertiary); }
.fr-card-body { display: none; padding: 0 1rem 0.875rem; }
.fr-card.open .fr-card-body { display: block; }
.fr-row { display: flex; align-items: center; gap: 0.5625rem; border-top: 1px solid var(--ui-stroke-tertiary); padding: 0.4375rem 0; font-size: 0.78125rem; color: var(--ui-text-primary); }
.fr-row.fr-muted { color: var(--ui-text-tertiary); }
.fr-dot { width: 0.375rem; height: 0.375rem; border-radius: 9999px; background: var(--ui-accent); }
.fr-label { display: block; margin-bottom: 0.375rem; font-size: 0.75rem; color: var(--ui-text-secondary); }
.fr-dots { display: flex; gap: 0.375rem; }
.fr-dots i { width: 0.3125rem; height: 0.3125rem; border-radius: 9999px; background: var(--ui-bg-tertiary); }
.fr-dots i.on { background: var(--ui-accent); }
.fr-dots i.done { background: color-mix(in srgb, var(--ui-accent) 55%, transparent); }
`

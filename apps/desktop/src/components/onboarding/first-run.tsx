import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
/**
 * 首次安装那十步（WP-ONBOARDING-WELCOME-FLOW）。
 *
 * 形状照本机 Antigravity 的欢迎向导（它的 app.asar 里内嵌了一整份独立向导 HTML）：
 * **一步一屏、居中一列、图标 ＋ 一句标题 ＋ 一段话 ＋ 至多一个主按钮**；动效是
 * **0.4s 淡入上移 12px** ＋ **三点呼吸 loader** 配「准备中…」（主人 11-18 13:5x：
 * 「我觉得 Antigravity 的引导界面挺好看的」）。
 *
 * 步序钉死（主人 11-17 20:0x 原话）：
 * 欢迎动效 → 界面 → 语言 → 主题 → 三张卡（点开展开里面支持的多家）→ 配模型 →
 * 画像（初步）→ 引导问答 → 给 Conductor 命名 → 问用户期望的称呼。
 *
 * 三张卡的名字**直接读设置页那三栏**（`t.settings.nav.provider*`）——主人 11-17 拍过
 * 「沿用设置页上的」⇒ 两处不许各说各话。
 *
 * 末步的答案落**两份文件**（主人 11-18 13:5x）：关于**用户的**（画像 ＋ 期望的称呼）⇒
 * `USER.md`；关于**Agent 自己**的（名字 ＋ 性格）⇒ 顶层 `SOUL.md`。见
 * `plobi_cli/onboarding_profile.py`。
 */
import { useEffect, useMemo, useState } from 'react'

import { LanguageSwitcher } from '@/components/language-switcher'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { type Locale, LOCALE_META, useI18n } from '@/i18n'
import { listOAuthProviders, setOnboardingProfile } from '@/plobi'
import { $connectedDesktopApps } from '@/store/desktop-quotas'
import { useTheme } from '@/themes/context'

import type { OnboardingContext } from '../../store/onboarding'

/** 十步的步序（判据盯的就是这一串）。 */
export const FIRST_RUN_STEPS = [
  'welcome',
  'interface',
  'language',
  'theme',
  'cards',
  'model',
  'profile',
  'agentName',
  'userTitle',
  'done'
] as const

export type FirstRunStep = (typeof FIRST_RUN_STEPS)[number]

const STEP_INDEX: Record<FirstRunStep, number> = Object.fromEntries(
  FIRST_RUN_STEPS.map((step, i) => [step, i])
) as Record<FirstRunStep, number>

export interface FirstRunFlowProps {
  ctx: OnboardingContext
  /** 「配模型」那一步的槽位：现有的 provider 选择器（不复制一份新的逻辑）。 */
  renderModel: () => React.ReactNode
  /** 末步落完两份文件之后。 */
  onCompleted?: () => void
  /** 从哪儿起：`intro` = 欢迎那几屏；`profile` = 配完模型之后回来（画像那几屏）。 */
  startAt?: 'intro' | 'profile'
  /** 「三张卡」那一步按继续 ⇒ 交回**既有**的 provider 选择器去配模型。 */
  onModelStep?: () => void
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

export function FirstRunFlow({
  ctx,
  renderModel,
  onCompleted,
  startAt = 'intro',
  onModelStep
}: FirstRunFlowProps) {
  const { t } = useI18n()
  const { mode, setMode } = useTheme()
  const [step, setStep] = useState<FirstRunStep>(startAt === 'profile' ? 'profile' : 'welcome')
  const [answers, setAnswers] = useState<Answers>(EMPTY_ANSWERS)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const quotaApps = useStore($connectedDesktopApps)

  const { data: oauthProviders } = useQuery({
    queryKey: ['first-run-oauth-providers'],
    queryFn: async () => (await listOAuthProviders()).providers,
    staleTime: 5 * 60 * 1000
  })

  const copy = t.onboarding.firstRun
  const index = STEP_INDEX[step]

  // 欢迎那一屏自己走完（Antigravity 那样：图标淡入 → 三点呼吸 → 下一句）。
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

  const back = () => {
    if (index <= 0) {
      return
    }

    setError('')
    setStep(FIRST_RUN_STEPS[index - 1])
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

  const desktopApps = useMemo(
    () =>
      Object.values(quotaApps)
        .filter((app) => app.connected && !app.optedOut)
        .sort((a, b) => a.id.localeCompare(b.id)),
    [quotaApps]
  )

  return (
    <div className="fixed inset-0 z-1300 flex items-center justify-center bg-black p-6">
      <style>{FIRST_RUN_CSS}</style>
      <div className="flex w-full max-w-[480px] flex-col items-center">
        <div className="flex min-h-[22rem] w-full flex-col items-center" key={step}>
          {step === 'welcome' ? <WelcomeStep copy={copy} /> : null}
          {step === 'interface' ? <InterfaceStep copy={copy} /> : null}
          {step === 'language' ? <LanguageStep copy={copy} /> : null}
          {step === 'theme' ? (
            <ThemeStep
              copy={copy}
              mode={mode}
              onChange={id => {
                setMode(id)
              }}
            />
          ) : null}
          {step === 'cards' ? (
            <CardsStep
              copy={copy}
              desktopApps={desktopApps}
              oauthProviders={oauthProviders ?? []}
            />
          ) : null}
          {step === 'model' ? <ModelStep copy={copy}>{renderModel()}</ModelStep> : null}
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

        {error ? <p className="mt-4 text-center text-xs text-red-400">{error}</p> : null}

        <StepFooter
          copy={copy}
          index={index}
          onBack={back}
          onNext={() => {
            // 「三张卡」那一步按继续 ⇒ 交回**既有**的 provider 选择器去配模型
            //（那一步的逻辑一个字不动；配完再回到画像那几屏）。
            if (step === 'cards') {
              onModelStep?.()

              return
            }

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

              return
            }

            go(FIRST_RUN_STEPS[index + 1])
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

type Copy = ReturnType<typeof useI18n>['t']['onboarding']['firstRun']

function WelcomeStep({ copy }: { copy: Copy }) {
  return (
    <Screen>
      <div className="fr-icon">
        <svg aria-hidden="true" fill="none" height="40" stroke="currentColor" strokeWidth="1.4" viewBox="0 0 24 24">
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
        <svg aria-hidden="true" fill="none" height="40" stroke="currentColor" strokeWidth="1.4" viewBox="0 0 24 24">
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
      <div className="hidden">
        <LanguageSwitcher />
      </div>
    </Screen>
  )
}

function ThemeStep({
  copy,
  mode,
  onChange
}: {
  copy: Copy
  mode: 'light' | 'dark' | 'system'
  onChange: (mode: 'light' | 'dark' | 'system') => void
}) {
  return (
    <Screen>
      <Title>{copy.themeTitle}</Title>
      <Desc>{copy.themeDesc}</Desc>
      <SegmentedControl
        className="mt-1"
        onChange={onChange}
        options={[
          { id: 'light', label: copy.themeLight },
          { id: 'dark', label: copy.themeDark },
          { id: 'system', label: copy.themeSystem }
        ]}
        value={mode}
      />
    </Screen>
  )
}

function CardsStep({
  copy,
  oauthProviders,
  desktopApps
}: {
  copy: Copy
  oauthProviders: { name?: string; id: string }[]
  desktopApps: { id: string; name?: string }[]
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState<'accounts' | 'keys' | 'desktop'>('accounts')

  return (
    <Screen>
      <Title>{copy.cardsTitle}</Title>
      <Desc>{copy.cardsDesc}</Desc>
      <div className="fr-cards">
        <ProviderCard
          body={
            oauthProviders.length > 0 ? (
              oauthProviders.map(p => (
                <div className="fr-row" key={p.id}>
                  <span className="fr-dot" />
                  <span>{p.name ?? p.id}</span>
                </div>
              ))
            ) : (
              <div className="fr-row">{t.settings.nav.providerAccounts}</div>
            )
          }
          label={t.settings.nav.providerAccounts}
          onClick={() => setOpen(open === 'accounts' ? 'accounts' : 'accounts')}
          open={open === 'accounts'}
        />
        <ProviderCard
          body={<div className="fr-row">{t.settings.nav.providerApiKeys}</div>}
          label={t.settings.nav.providerApiKeys}
          onClick={() => setOpen('keys')}
          open={open === 'keys'}
        />
        <ProviderCard
          body={
            desktopApps.length > 0 ? (
              desktopApps.map(app => (
                <div className="fr-row" key={app.id}>
                  <span className="fr-dot" />
                  <span>{app.name ?? app.id}</span>
                </div>
              ))
            ) : (
              <div className="fr-row">{t.settings.nav.providerSubscriptions}</div>
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

function ModelStep({ copy, children }: { copy: Copy; children: React.ReactNode }) {
  return (
    <Screen>
      <Title>{copy.modelTitle}</Title>
      <Desc>{copy.modelDesc}</Desc>
      <div className="w-full">{children}</div>
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
        <svg aria-hidden="true" fill="none" height="40" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
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
  step,
  index,
  saving,
  onBack,
  onNext,
  onSkip
}: {
  copy: Copy
  step: FirstRunStep
  index: number
  saving: boolean
  onBack: () => void
  onNext: () => void
  onSkip: () => void
}) {
  const skippable = step === 'model' || step === 'profile' || step === 'agentName' || step === 'userTitle'
  const last = step === 'done'

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

/** 形状：Antigravity 那套（黑底、0.4s 淡入上移 12px、三点呼吸 loader、12px 圆角）。 */
const FIRST_RUN_CSS = `
@keyframes fr-fade-in { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
@keyframes fr-dot-pulse { 0%, 100% { opacity: 0.2; transform: scale(0.9); } 50% { opacity: 1; transform: scale(1.1); } }
.fr-screen { display: flex; width: 100%; flex-direction: column; align-items: center; text-align: center; animation: fr-fade-in 0.4s ease; }
.fr-title { font-size: 1.375rem; font-weight: 600; letter-spacing: -0.01em; margin-bottom: 0.5rem; }
.fr-desc { font-size: 0.8125rem; line-height: 1.6; color: var(--ui-text-secondary); margin-bottom: 1.25rem; }
.fr-icon { display: flex; width: 4.75rem; height: 4.75rem; align-items: center; justify-content: center; border-radius: 1.25rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-tertiary); margin-bottom: 1.25rem; }
.fr-loader { display: flex; gap: 0.45rem; margin-bottom: 1rem; }
.fr-loader i { width: 0.45rem; height: 0.45rem; border-radius: 9999px; background: var(--ui-text-secondary); animation: fr-dot-pulse 1.1s ease-in-out infinite; }
.fr-loader i:nth-child(2) { animation-delay: 0.18s; }
.fr-loader i:nth-child(3) { animation-delay: 0.36s; }
.fr-list { display: flex; width: 100%; flex-direction: column; gap: 0.5rem; }
.fr-item { width: 100%; border-radius: 0.5rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-secondary); padding: 0.75rem 0.875rem; text-align: left; font-size: 0.8125rem; }
.fr-item[aria-pressed='true'] { border-color: var(--ui-accent); }
.fr-cards { display: flex; width: 100%; flex-direction: column; gap: 0.625rem; margin-bottom: 1.25rem; }
.fr-card { border-radius: 0.75rem; border: 1px solid var(--ui-stroke-tertiary); background: var(--ui-bg-secondary); overflow: hidden; text-align: left; }
.fr-card-head { display: flex; width: 100%; align-items: center; gap: 0.75rem; padding: 0.875rem 1rem; font-size: 0.8125rem; font-weight: 500; }
.fr-chev { margin-left: auto; color: var(--ui-text-tertiary); }
.fr-card-body { display: none; padding: 0 1rem 0.875rem; }
.fr-card.open .fr-card-body { display: block; }
.fr-row { display: flex; align-items: center; gap: 0.5625rem; border-top: 1px solid var(--ui-stroke-tertiary); padding: 0.4375rem 0; font-size: 0.78125rem; }
.fr-dot { width: 0.375rem; height: 0.375rem; border-radius: 9999px; background: var(--ui-accent); }
.fr-label { display: block; margin-bottom: 0.375rem; text-align: left; font-size: 0.75rem; color: var(--ui-text-secondary); }
.fr-dots { display: flex; gap: 0.375rem; }
.fr-dots i { width: 0.3125rem; height: 0.3125rem; border-radius: 9999px; background: var(--ui-bg-tertiary); }
.fr-dots i.on { background: var(--ui-accent); }
.fr-dots i.done { background: color-mix(in srgb, var(--ui-accent) 55%, transparent); }
`

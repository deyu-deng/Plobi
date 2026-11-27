import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { KbdCombo } from '@/components/ui/kbd'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { AudioLines, iconSize, Layers3, Loader2, Square, SteeringWheel, Volume2, VolumeX } from '@/lib/icons'
import { formatCombo } from '@/lib/keybinds/combo'
import { cn } from '@/lib/utils'

import { composerControlsFor } from '../../shell/slot-registry'
import { useSlotRegistry } from '../../shell/slot-registry-context'
import type { ConversationStatus } from './hooks/use-voice-conversation'
import { ModelPill } from './model-pill'
import type { ChatBarState, VoiceStatus } from './types'

export const ICON_BTN = 'size-(--composer-control-size) shrink-0 rounded-md'
export const GHOST_ICON_BTN = cn(
  ICON_BTN,
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
)
// Send/voice-conversation primary: solid foreground-on-background circle
// (reads as black-on-white in light mode, white-on-black in dark mode) to
// match the reference composer's high-contrast CTA. Keeps the pill itself
// neutral and lets the action visually dominate the row.
export const PRIMARY_ICON_BTN = cn(
  'size-(--composer-control-primary-size,var(--composer-control-size)) shrink-0 rounded-full p-0',
  'bg-foreground text-background hover:bg-foreground/90',
  'disabled:bg-foreground/30 disabled:text-background disabled:opacity-100'
)

interface ConversationProps {
  active: boolean
  level: number
  muted: boolean
  status: ConversationStatus
  onEnd: () => void
  onStart: () => void
  onStopTurn: () => void
  onToggleMute: () => void
}

export function ComposerControls({
  autoSpeak,
  busy,
  busyAction,
  canSteer,
  canSubmit,
  compactModelPill = false,
  conversation,
  disabled,
  hasComposerPayload,
  state,
  voiceStatus,
  onDictate,
  onSteer,
  onToggleAutoSpeak
}: {
  autoSpeak: boolean
  busy: boolean
  /**
   * 忙时那枚按钮干什么＝**底座那一个键**（`display.busy_input_mode`）：
   * `queue` 排队／`interrupt` 打断并发／`steer` 转向；`stop` 是空着的时候。
   */
  busyAction: 'interrupt' | 'queue' | 'steer' | 'stop'
  canSteer: boolean
  canSubmit: boolean
  compactModelPill?: boolean
  conversation: ConversationProps
  disabled: boolean
  hasComposerPayload: boolean
  state: ChatBarState
  voiceStatus: VoiceStatus
  onDictate: () => void
  onSteer: () => void
  onToggleAutoSpeak: () => void
}) {
  const { t } = useI18n()
  const c = t.composer
  const navigate = useNavigate()
  const registry = useSlotRegistry()
  const steerCombo = formatCombo('mod+enter')
  const steerLabel = `${c.steer} (${steerCombo})`

  const steerTip = (
    <span className="inline-flex items-center gap-1.5">
      {c.steer}
      <KbdCombo combo="mod+enter" size="sm" variant="inverted" />
    </span>
  )

  if (conversation.active) {
    return <ConversationPill {...conversation} disabled={disabled} />
  }

  const showVoicePrimary = !busy && !hasComposerPayload

  // 忙时那枚主按钮＝底座那一个键说的：
  // - `queue`：排队（面板上看得见那一条）；
  // - `interrupt`：打断当前这一轮并把这句话送出去（送＝向上箭头）；
  // - `steer`：转向当前这一轮（转向轮）；
  // - 空着的时候：停止（方块）。
  // 形状用现成的 i18n 文案配对，不新增任何屏上文案。
  const busyButton = (() => {
    if (!busy) {
      return { icon: <Codicon name="arrow-up" size="0.875rem" />, label: c.send }
    }

    if (busyAction === 'queue') {
      return { icon: <Layers3 className={iconSize.sm} />, label: c.queueMessage }
    }

    if (busyAction === 'interrupt') {
      return { icon: <Codicon name="arrow-up" size="0.875rem" />, label: c.send }
    }

    if (busyAction === 'steer') {
      return { icon: <SteeringWheel className={iconSize.sm} />, label: c.steer }
    }

    return { icon: <span className="block size-2.5 rounded-2xs bg-current" />, label: c.stop }
  })()

  return (
    <div className="ml-auto flex shrink-0 items-center gap-(--composer-control-gap)">
      {/* 那一排**从 slot 声明表派生**（用户 11-27 22:0x 点的第②块）：宿主自己的控件由
          宿主按 id 渲染（悬停文案照旧跟着状态变）；插件加的那一格＝**图标＋名字＋去哪**，
          点它跳过去 ⇒ 与左栏那排同一种形状。 */}
      {composerControlsFor(registry).map(item => {
        if (item.control === 'model-pill') {
          return <ModelPill compact={compactModelPill} disabled={disabled} key={item.id} model={state.model} />
        }

        if (item.control === 'dictation') {
          // While the agent runs and the user is typing, steer takes over the mic's
          // slot rather than crowding the row with an extra button. In `steer`
          // mode the primary button IS the steer button — two wheels on one row
          // would be noise, so the ghost one stands down.
          if (canSteer && busyAction !== 'steer') {
            return (
              <Tip key={item.id} label={steerTip}>
                <Button
                  aria-label={steerLabel}
                  className={GHOST_ICON_BTN}
                  disabled={disabled}
                  onClick={onSteer}
                  size="icon"
                  type="button"
                  variant="ghost"
                >
                  <SteeringWheel className={iconSize.sm} />
                </Button>
              </Tip>
            )
          }

          return <DictationButton disabled={disabled} key={item.id} onToggle={onDictate} state={state.voice} status={voiceStatus} />
        }

        if (item.control === 'auto-speak') {
          return <AutoSpeakButton active={autoSpeak} disabled={disabled} key={item.id} onToggle={onToggleAutoSpeak} />
        }

        if (item.control === 'primary') {
          return showVoicePrimary ? (
            <Tip key={item.id} label={c.startVoice}>
              <Button
                aria-label={c.startVoice}
                className={PRIMARY_ICON_BTN}
                disabled={disabled}
                onClick={() => {
                  triggerHaptic('open')
                  conversation.onStart()
                }}
                size="icon"
                type="button"
              >
                <AudioLines className={iconSize.sm} />
              </Button>
            </Tip>
          ) : (
            <Tip key={item.id} label={busyButton.label}>
              <Button
                aria-label={busyButton.label}
                className={PRIMARY_ICON_BTN}
                disabled={disabled || !canSubmit}
                type="submit"
              >
                {busyButton.icon}
              </Button>
            </Tip>
          )
        }

        // 插件加的那一格：图标＋名字 ⇒ 点它跳过去（它自己的动作是「深」的那一步）。
        if (!item.route) {
          return null
        }

        return (
          <Tip key={item.id} label={item.label ?? item.id}>
            <Button
              aria-label={item.label ?? item.id}
              className={GHOST_ICON_BTN}
              onClick={() => navigate(item.route as string)}
              size="icon"
              type="button"
              variant="ghost"
            >
              <Codicon name={item.icon ?? 'symbol-misc'} size="0.875rem" />
            </Button>
          </Tip>
        )
      })}
    </div>
  )
}

function ConversationPill({
  disabled,
  level,
  muted,
  onEnd,
  onStopTurn,
  onToggleMute,
  status
}: ConversationProps & { disabled: boolean }) {
  const { t } = useI18n()
  const c = t.composer
  const speaking = status === 'speaking'
  const listening = status === 'listening' && !muted

  const label =
    status === 'speaking'
      ? c.speaking
      : status === 'transcribing'
        ? c.transcribing
        : status === 'thinking'
          ? c.thinking
          : muted
            ? c.muted
            : c.listening

  return (
    <div className="ml-auto flex shrink-0 items-center gap-(--composer-control-gap)">
      <Tip label={muted ? c.unmuteMic : c.muteMic}>
        <Button
          aria-label={muted ? c.unmuteMic : c.muteMic}
          aria-pressed={muted}
          className={cn(GHOST_ICON_BTN, 'p-0', muted && 'bg-muted text-muted-foreground')}
          disabled={disabled}
          onClick={() => {
            triggerHaptic('selection')
            onToggleMute()
          }}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Codicon name={muted ? 'mic-off' : 'mic'} size="1rem" />
        </Button>
      </Tip>
      {listening && (
        <Button
          aria-label={c.stopListening}
          className="h-(--composer-control-size) shrink-0 gap-1.5 rounded-full px-2.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          disabled={disabled}
          onClick={() => {
            triggerHaptic('submit')
            onStopTurn()
          }}
          title={c.stopListening}
          type="button"
          variant="ghost"
        >
          <Square className={cn('fill-current', iconSize.xs)} />
          <span>{c.stopShort}</span>
        </Button>
      )}
      <Button
        aria-label={c.endConversation}
        className="h-(--composer-control-size) gap-1.5 rounded-full bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"
        disabled={disabled}
        onClick={() => {
          triggerHaptic('close')
          onEnd()
        }}
        title={c.endConversation}
        type="button"
      >
        <ConversationIndicator level={level} listening={listening} speaking={speaking} />
        <span>{c.endShort}</span>
      </Button>
      <span className="sr-only" role="status">
        {label}
      </span>
    </div>
  )
}

function ConversationIndicator({
  level,
  listening,
  speaking
}: {
  level: number
  listening: boolean
  speaking: boolean
}) {
  if (speaking) {
    return <Loader2 className={cn('animate-spin', iconSize.xs)} />
  }

  const bars = [0.55, 0.85, 1, 0.85, 0.55]
  const normalized = Math.max(0, Math.min(level, 1))

  return (
    <span aria-hidden="true" className="flex h-3 items-center gap-0.5">
      {bars.map((weight, index) => {
        const height = listening ? 0.3 + Math.min(0.7, normalized * weight) : 0.3

        return <span className="w-0.5 rounded-full bg-current" key={index} style={{ height: `${height * 100}%` }} />
      })}
    </span>
  )
}

// Pure-TTS toggle: type normally, but have every assistant reply read aloud —
// no dictation, no full conversation loop. Filled/accent when on, mirroring the
// muted-mic pressed state above. Driven by (and persisted to) `voice.auto_tts`.
function AutoSpeakButton({ active, disabled, onToggle }: { active: boolean; disabled: boolean; onToggle: () => void }) {
  const { t } = useI18n()
  const c = t.composer
  const label = active ? c.stopSpeakingReplies : c.speakReplies

  return (
    <Tip label={label}>
      <Button
        aria-label={label}
        aria-pressed={active}
        className={cn(
          GHOST_ICON_BTN,
          'p-0',
          active && 'bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary'
        )}
        disabled={disabled}
        onClick={() => {
          triggerHaptic(active ? 'close' : 'open')
          onToggle()
        }}
        size="icon"
        type="button"
        variant="ghost"
      >
        {active ? <Volume2 className={iconSize.sm} /> : <VolumeX className={iconSize.sm} />}
      </Button>
    </Tip>
  )
}

function DictationButton({
  disabled,
  state,
  status,
  onToggle
}: {
  disabled: boolean
  state: ChatBarState['voice']
  status: VoiceStatus
  onToggle: () => void
}) {
  const { t } = useI18n()
  const c = t.composer
  const active = state.active || status !== 'idle'

  const aria =
    status === 'recording' ? c.stopDictation : status === 'transcribing' ? c.transcribingDictation : c.voiceDictation

  return (
    <Tip label={aria}>
      <Button
        aria-label={aria}
        aria-pressed={active}
        className={cn(
          GHOST_ICON_BTN,
          'p-0',
          'data-[active=true]:bg-accent data-[active=true]:text-foreground',
          status === 'recording' && 'bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary',
          status === 'transcribing' && 'bg-primary/10 text-primary'
        )}
        data-active={active}
        disabled={disabled || !state.enabled || status === 'transcribing'}
        onClick={() => {
          triggerHaptic(active ? 'close' : 'open')
          onToggle()
        }}
        size="icon"
        type="button"
        variant="ghost"
      >
        {status === 'recording' ? (
          <Square className={cn('fill-current', iconSize.xs)} />
        ) : status === 'transcribing' ? (
          <Loader2 className={cn('animate-spin', iconSize.sm)} />
        ) : (
          <Codicon name="mic" size="0.875rem" />
        )}
      </Button>
    </Tip>
  )
}

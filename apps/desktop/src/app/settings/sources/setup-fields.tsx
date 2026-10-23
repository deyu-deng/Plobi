/**
 * 点开一行之后那片抽屉里的表单。
 *
 * **格子是后端画出来的，不是这里硬编的**——`row.setup_fields` 来自那条信源自己的
 * `SourceSpec.setup_fields` 声明。加一条新信源不用改 `apps/desktop/**`（spec §8 有一条
 * 验收就是这个），代价是**这里不许出现任何具体信源的字段名**。
 *
 * 标签查语言目录的 `settings.sources.fields.<key>`，查不到才回后端给的英文缺省值
 * ——所以第三方包零改动就能用，只是没翻译。
 *
 * 10-22 起有两种控件不再是"一个文本框"（用户：「密码什么的就不要明文显示。用星号」
 * 「频率也莫名奇妙的用一个英文，太丑了」）：
 *
 * - **`secret`**：`type=password`，而且**这里从来没有它的旧值**——后端读数里只有
 *   `secrets[key] = 设过没有`。所以这一格是写入口，不是读数口：留空＝不改（清空要有显式动作，
 *   否则一次没填就把凭据弄没了）。
 * - **`cadence`**：**人按的控件，不是让人读 cron**（用户 10-23 看截图：「频率设置这样用机器语言
 *   还是不好，可能可以用一个可交互的旋钮来设置采集频率」）。四种模式各配一个真正的控件：
 *   每一轮（什么都不用填）／每隔几小时（滑杆）／每天（选时间）／每周（选星期 + 时间）。
 *   存进 `sources.yaml` 的**仍是那一句 cron**——运行时一个字没改，改的只是画法（裁定 191 二）。
 *   「自定义表达式」还在但**收起来**：解析器认的写法比这四档多，锁死是把能力做窄；
 *   可默认让普通人面对一个裸 cron 框，正是这一屏上一次被否掉的那个形状。
 */

import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { notifyError } from '@/store/notifications'

import type { SetupField, SourceRow } from './api'
import { saveSourceSetup } from './api'

type Labels = {
  cadenceAdvanced: string
  cadenceEveryHours: string
  cadenceModeDaily: string
  cadenceModeEveryHours: string
  cadenceModeEveryRound: string
  cadenceModeWeekly: string
  cadenceWeekdays: Record<string, string>
  fields: Record<string, string>
  saveFailed: string
  saveSetup: string
}

type CadenceMode = 'custom' | 'daily' | 'every_hours' | 'every_round' | 'weekly'

/** 读一句存量 cron → 控件该停在哪一档。认不出就落「自定义」，**不猜**。 */
export function parseCadence(value: string): {
  dow: number
  hour: number
  hours: number
  minute: number
  mode: CadenceMode
  raw: string
} {
  const raw = value.trim()
  const base = { dow: 1, hour: 3, hours: 4, minute: 0, mode: 'custom' as CadenceMode, raw }

  if (!raw) {
    return { ...base, mode: 'every_round' }
  }

  const legacy = /^(?:every\s*|每\s*)(\d{1,2})\s*h$/i.exec(raw)

  if (legacy) {
    return { ...base, hours: clampHours(Number(legacy[1])), mode: 'every_hours' }
  }

  const stepped = /^(\d{1,2})\s+\*\/(\d{1,2})\s+\*\s+\*\s+\*$/.exec(raw)

  if (stepped) {
    return { ...base, hours: clampHours(Number(stepped[2])), minute: Number(stepped[1]), mode: 'every_hours' }
  }

  const hourly = /^(\d{1,2})\s+\*\s+\*\s+\*\s+\*$/.exec(raw)

  if (hourly) {
    return { ...base, hours: 1, minute: Number(hourly[1]), mode: 'every_hours' }
  }

  const daily = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/.exec(raw)

  if (daily) {
    return { ...base, hour: Number(daily[2]), minute: Number(daily[1]), mode: 'daily' }
  }

  const weekly = /^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+([0-7])$/.exec(raw)

  if (weekly) {
    return {
      ...base,
      dow: Number(weekly[3]) % 7,
      hour: Number(weekly[2]),
      minute: Number(weekly[1]),
      mode: 'weekly'
    }
  }

  return base
}

function clampHours(hours: number): number {
  return Number.isFinite(hours) ? Math.min(12, Math.max(1, Math.round(hours))) : 4
}

function two(value: number): string {
  return String(Math.max(0, Math.min(59, Math.round(value)))).padStart(2, '0')
}

/** 控件停的那一档 → 存进配置的那一句。空串＝跟着全局每一轮。 */
export function cadenceCron(input: {
  dow: number
  hour: number
  hours: number
  minute: number
  mode: CadenceMode
  raw: string
}): string {
  switch (input.mode) {
    case 'every_round': {
      return ''
    }

    case 'every_hours': {
      return input.hours === 1
        ? `${two(input.minute)} * * * *`
        : `${two(input.minute)} */${input.hours} * * *`
    }

    case 'daily': {
      return `${two(input.minute)} ${two(input.hour)} * * *`
    }

    case 'weekly': {
      return `${two(input.minute)} ${two(input.hour)} * * ${input.dow % 7}`
    }

    default: {
      return input.raw
    }
  }
}

export function SetupFields({
  busy,
  focusFirst,
  labels,
  onSaved,
  row
}: {
  busy: boolean
  /** 第一次点开关时由调用方置真：直接聚焦第一格，别让光标落在"保存"上。 */
  focusFirst?: boolean
  labels: Labels
  onSaved: () => Promise<void>
  row: SourceRow
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  // 读数变了就把表单拉回后端那份（用户刚保存、或别人在别的窗口改了）。
  // 不这么做：保存成功后表单还留着上一份，下次进来看到的是过期读数。
  useEffect(() => {
    const next: Record<string, string> = {}

    for (const field of row.setup_fields) {
      // 密钥那一格**永远从空开始**：后端不给旧值，这里也不该假装有一个。
      next[field.key] = field.kind === 'secret' ? '' : String((row.setup ?? {})[field.key] ?? '')
    }

    setValues(next)
  }, [row.source, row.setup, row.setup_fields, row.secrets])

  const missing = row.setup_fields.filter(field => field.required && !values[field.key]?.trim()
    && !(field.kind === 'secret' && row.secrets?.[field.key] === true))

  const save = async () => {
    if (missing.length > 0) {
      return
    }

    setSaving(true)

    try {
      const params: Record<string, string> = {}

      for (const field of row.setup_fields) {
        const value = values[field.key]?.trim()

        // 空的**不送**：后端按声明校验必填，送一个空串等于把"没填"伪装成"填了空的"；
        // 密钥那一格还多一层意思——留空＝不改，不是清空。
        // 频率那一格是例外：**空串就是"每一轮"**，不送就永远回不到那一档（10-23 修）。
        if (value || field.kind === 'cadence') {
          params[field.key] = value ?? ''
        }
      }

      await saveSourceSetup(row.source, params)
      await onSaved()
    } catch (err) {
      notifyError(err, labels.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 space-y-2" data-testid={`setup-${row.source}`}>
      {row.setup_fields.map((field, index) => (
        <FieldInput
          autoFocus={focusFirst && index === 0}
          busy={busy || saving}
          field={field}
          key={field.key}
          label={labels.fields[field.key] ?? field.label}
          labels={labels}
          onChange={value => setValues(prev => ({ ...prev, [field.key]: value }))}
          value={values[field.key] ?? ''}
        />
      ))}

      <Button disabled={busy || saving || missing.length > 0} onClick={() => { void save() }} size="sm" variant="secondary">
        {labels.saveSetup}
      </Button>
    </div>
  )
}

function FieldInput({
  autoFocus,
  busy,
  field,
  label,
  labels,
  onChange,
  value
}: {
  autoFocus?: boolean
  busy: boolean
  field: SetupField
  label: string
  labels: Labels
  onChange: (value: string) => void
  value: string
}) {
  return (
    // **不是 `<label>`**：频率那一档里是一组按钮 + 滑杆 + 时间框，一条 label 管不了四样，
    // 而且按钮放进 label 之后无障碍名会被吃掉（10-23 测试里当场撞见：`getByRole('button')` 找不到）。
    // 每一件自己带 `aria-label`，读数与读屏都还在。
    <div className="block">
      <span className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary)">
        {label}
        {field.required ? <span aria-hidden="true"> *</span> : null}
      </span>

      {field.kind === 'cadence' ? (
        <CadenceField labels={labels} onChange={onChange} value={value} />
      ) : (
        <Input
          aria-label={label}
          autoFocus={autoFocus}
          className="mt-1"
          disabled={busy}
          onChange={event => onChange(event.target.value)}
          placeholder={placeholderFor(field, labels)}
          type={field.kind === 'secret' ? 'password' : field.kind === 'url' ? 'url' : 'text'}
          value={value}
        />
      )}
    </div>
  )
}

function placeholderFor(field: SetupField, labels: Labels): string | undefined {
  if (field.kind === 'secret') {
    return undefined
  }

  return field.kind === 'path' ? field.help : undefined
}

/**
 * 频率控件：四档 + 一个收起来的「自定义表达式」。
 *
 * **不用 `Select`**：用户 10-23 那一句「可交互的旋钮」要的是"能直接拨"的东西——滑杆与时间选择器
 * 拨一下就变，而下拉框是"点两下再从列表里认一行"。两处都不是自由文本，cron 只在最后那一档出现。
 */
function CadenceField({ labels, onChange, value }: {
  labels: Labels
  onChange: (value: string) => void
  value: string
}) {
  const parsed = parseCadence(value)
  const [advanced, setAdvanced] = useState(parsed.mode === 'custom')
  const update = (patch: Partial<typeof parsed>) => onChange(cadenceCron({ ...parsed, ...patch }))

  const modes: { key: CadenceMode; label: string }[] = [
    { key: 'every_round', label: labels.cadenceModeEveryRound },
    { key: 'every_hours', label: labels.cadenceModeEveryHours },
    { key: 'daily', label: labels.cadenceModeDaily },
    { key: 'weekly', label: labels.cadenceModeWeekly }
  ]

  return (
    <div className="mt-1 space-y-2" data-cadence={value} data-testid="cadence-field">
      <div aria-label={labels.fields.cadence ?? 'cadence'} className="flex flex-wrap gap-1" role="group">
        {modes.map(mode => (
          <button
            aria-pressed={parsed.mode === mode.key}
            className={parsed.mode === mode.key
              ? 'rounded-md bg-(--ui-bg-secondary) px-2 py-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-primary)'
              : 'rounded-md px-2 py-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary) hover:bg-(--ui-bg-secondary)'}
            key={mode.key}
            onClick={() => { setAdvanced(false); update({ mode: mode.key }) }}
            type="button"
          >
            {mode.label}
          </button>
        ))}
      </div>

      {parsed.mode === 'every_hours' ? (
        <label className="flex items-center gap-2 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary)">
          <input
            aria-label={labels.cadenceModeEveryHours}
            className="min-w-0 flex-1 accent-(--ui-text-primary)"
            max={12}
            min={1}
            onChange={event => update({ hours: clampHours(Number(event.target.value)) })}
            type="range"
            value={parsed.hours}
          />
          <span className="shrink-0 text-(--ui-text-primary)">
            {labels.cadenceEveryHours.replace('{n}', String(parsed.hours))}
          </span>
        </label>
      ) : null}

      {parsed.mode === 'daily' || parsed.mode === 'weekly' ? (
        <div className="flex flex-wrap items-center gap-1">
          {parsed.mode === 'weekly'
            ? ['1', '2', '3', '4', '5', '6', '0'].map(day => (
                <button
                  aria-pressed={parsed.dow === Number(day)}
                  className={parsed.dow === Number(day)
                    ? 'rounded-md bg-(--ui-bg-secondary) px-2 py-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-primary)'
                    : 'rounded-md px-2 py-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary) hover:bg-(--ui-bg-secondary)'}
                  key={day}
                  onClick={() => update({ dow: Number(day) })}
                  type="button"
                >
                  {labels.cadenceWeekdays[day] ?? day}
                </button>
              ))
            : null}
          <Input
            aria-label={labels.fields.cadence ?? 'time'}
            className="w-32"
            onChange={event => {
              const [hour, minute] = event.target.value.split(':')

              if (hour && minute) {
                update({ hour: Number(hour), minute: Number(minute) })
              }
            }}
            type="time"
            value={`${two(parsed.hour)}:${two(parsed.minute)}`}
          />
        </div>
      ) : null}

      <button
        className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary) underline-offset-2 hover:underline"
        onClick={() => setAdvanced(prev => !prev)}
        type="button"
      >
        {labels.cadenceAdvanced}
      </button>

      {advanced ? (
        <Input
          aria-label={labels.cadenceAdvanced}
          className="font-mono"
          onChange={event => onChange(event.target.value)}
          onFocus={() => { if (parsed.mode !== 'custom') { onChange(value || '0 3 * * *') } }}
          placeholder="0 3 * * *"
          value={value}
        />
      ) : null}
    </div>
  )
}

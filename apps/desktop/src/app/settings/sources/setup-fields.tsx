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
 * - **`cadence`**：一组预设 + 「自定义」才出文本框。存进 `sources.yaml` 的**仍是那一句 cron**，
 *   运行时一个字没改——改的只是"别让用户面对一个裸 cron 框"。
 */

import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { notifyError } from '@/store/notifications'

import type { SetupField, SourceRow } from './api'
import { saveSourceSetup } from './api'

type Labels = {
  cadencePresets: Record<string, string>
  cadenceCustom: string
  fields: Record<string, string>
  saveFailed: string
  saveSetup: string
}

/** 预设的键 —— 界面上选它，存下去的是右边那句 cron。空串＝不设，跟着全局那一轮。 */
export const CADENCE_PRESETS: Record<string, string> = {
  per_round: '',
  hourly: 'every 1h',
  four_hours: 'every 4h',
  six_hours: 'every 6h',
  daily: '0 3 * * *',
  weekly: '0 3 * * 1'
}

function presetKeyFor(cron: string): string {
  const found = Object.entries(CADENCE_PRESETS).find(([, value]) => value === cron.trim())

  return found?.[0] ?? 'custom'
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
        if (value) {
          params[field.key] = value
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
    <label className="block">
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
    </label>
  )
}

function placeholderFor(field: SetupField, labels: Labels): string | undefined {
  if (field.kind === 'secret') {
    return undefined
  }

  return field.kind === 'path' ? field.help : undefined
}

/**
 * 频率那一格：预设下拉，选「自定义」才出那个 cron 文本框。
 *
 * 为什么不是干脆禁掉自由输入：`cron/jobs.py` 那句解析器支持的写法比六个预设多得多
 * （`every 90m`、五段 cron、ISO 时间戳），把它锁死是**把能力做窄**；要治的只是
 * "默认就摆一个普通人看不懂的框"。
 */
function CadenceField({ labels, onChange, value }: {
  labels: Labels
  onChange: (value: string) => void
  value: string
}) {
  const key = presetKeyFor(value)

  return (
    <div className="mt-1 space-y-1">
      <Select
        onValueChange={next => onChange(next === 'custom' ? value : CADENCE_PRESETS[next] ?? '')}
        value={key}
      >
        <SelectTrigger aria-label={labels.fields.cadence ?? 'cadence'} className="w-full" size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {Object.keys(CADENCE_PRESETS).map(preset => (
            <SelectItem key={preset} value={preset}>{labels.cadencePresets[preset] ?? preset}</SelectItem>
          ))}
          <SelectItem value="custom">{labels.cadenceCustom}</SelectItem>
        </SelectContent>
      </Select>

      {key === 'custom' ? (
        <Input
          aria-label={labels.cadenceCustom}
          className="font-mono"
          onChange={event => onChange(event.target.value)}
          placeholder="0 3 * * *"
          value={value}
        />
      ) : null}
    </div>
  )
}

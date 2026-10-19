/**
 * 首次配置那一小片表单。
 *
 * **格子是后端画出来的，不是这里硬编的**——`row.setup_fields` 来自那条信源自己的
 * `SourceSpec.setup_fields` 声明。加一条新信源不用改 `apps/desktop/**`（spec §8 有一条
 * 验收就是这个），代价是**这里不许出现任何具体信源的字段名**。
 *
 * 标签查语言目录的 `settings.sources.fields.<key>`，查不到才回后端给的英文缺省值
 * ——所以第三方包零改动就能用，只是没翻译。
 *
 * 有一个控件形状这里**故意不做**：`password`。密钥归 `.env`，而声明层压根不允许
 * 声明一个像密钥的字段（`registry.SetupField.__post_init__`），所以这里也没有地方
 * 需要它。**不要**因为"看着齐全"就加一个。
 */

import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { notifyError } from '@/store/notifications'

import type { SetupField, SourceRow } from './api'
import { saveSourceSetup } from './api'

type Labels = {
  fields: Record<string, string>
  saveFailed: string
  saveSetup: string
}

export function SetupFields({
  busy,
  labels,
  onSaved,
  row
}: {
  busy: boolean
  labels: Labels
  onSaved: () => Promise<void>
  row: SourceRow
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  // 读数变了就把表单拉回后端那份（用户刚保存、或别人在别的窗口改了）。
  // 不这么做：保存成功后表单还留着上一份，下次进来看到的是过期读数。
  // `setup_fields` 也在依赖里 —— 声明本身变了（装了新包）时表单要跟着重画，
  // 否则那一行会停在一个已经不存在的字段上。
  useEffect(() => {
    const next: Record<string, string> = {}

    for (const field of row.setup_fields) {
      next[field.key] = String((row.setup ?? {})[field.key] ?? '')
    }

    setValues(next)
  }, [row.source, row.setup, row.setup_fields])

  const missing = row.setup_fields.filter(field => field.required && !values[field.key]?.trim())

  const save = async () => {
    if (missing.length > 0) {
      return
    }

    setSaving(true)

    try {
      const params: Record<string, string> = {}

      for (const field of row.setup_fields) {
        const value = values[field.key]?.trim()

        // 空的**不送**：后端按声明校验必填，送一个空串等于把"没填"伪装成"填了空的"。
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
      {row.setup_fields.map(field => (
        <FieldInput
          busy={busy || saving}
          field={field}
          key={field.key}
          label={labels.fields[field.key] ?? field.label}
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
  busy,
  field,
  label,
  onChange,
  value
}: {
  busy: boolean
  field: SetupField
  label: string
  onChange: (value: string) => void
  value: string
}) {
  return (
    <label className="block">
      <span className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary)">
        {label}
        {field.required ? <span aria-hidden="true"> *</span> : null}
      </span>
      <Input
        aria-label={label}
        className="mt-1"
        disabled={busy}
        onChange={event => onChange(event.target.value)}
        placeholder={field.kind === 'path' ? field.help : undefined}
        type="text"
        value={value}
      />
    </label>
  )
}
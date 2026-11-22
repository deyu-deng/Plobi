import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'

import { AgentAppearanceFields, AgentFolderFields, AgentNameFields } from './agent-form-fields'
import { isValidEnglishName } from './create-agent'

/**
 * 「新建 Agent」与「编辑这个 Agent」**同一个容器**。
 *
 * 用户 2026-11-22 的第一刀只切到字段层（`agent-form-fields.tsx` 三块共用），可壳子
 * 还是两套：新建在侧栏里自己拼 Dialog + form + footer，编辑又拼一遍，于是提交按钮的
 * 禁用条件、错误提示的位置、Esc 该不该拦住、文件夹那一格要不要显示，全在两处各写一份。
 * 这一刀把壳子也收成一份——两个调用方只喂数据与文案，不再各写一遍布局。
 *
 * 位置顺序也是统一的（用户抱怨过「两边长得不一样」）：名字 → 中间那一格（新建=品类，
 * 编辑=改名清单）→ 工作目录 → 外观 → 错误 → footer。
 */
export function AgentFormDialog({
  appearanceLabels,
  busy,
  busyLabel,
  canSubmit,
  cancelLabel,
  color,
  description,
  displayName,
  englishHelp,
  englishLabel,
  englishName,
  error,
  extra,
  folderLabels,
  icon,
  idPrefix,
  nameLabel,
  onAddFolder,
  onCancel,
  onChangeColor,
  onChangeIcon,
  onClearAppearance,
  onDisplayNameChange,
  onEnglishNameChange,
  onOpenChange,
  onRemoveFolder,
  onRetranslate,
  onSubmit,
  open,
  paths,
  preview,
  retranslateLabel,
  showFolders = true,
  submitLabel,
  title,
  translating
}: {
  appearanceLabels: { clear: string; hint?: string; label: string }
  busy: boolean
  busyLabel: string
  canSubmit: boolean
  cancelLabel: string
  color: string
  description?: string
  displayName: string
  englishHelp: string
  englishLabel: string
  englishName: string
  error?: null | string
  extra?: ReactNode
  folderLabels: { add: string; empty: string; hint?: string; label: string; primary: string; remove: string }
  icon: string
  idPrefix: string
  nameLabel: string
  onAddFolder: () => void
  onCancel: () => void
  onChangeColor: (value: string) => void
  onChangeIcon: (value: string) => void
  onClearAppearance: () => void
  onDisplayNameChange: (value: string) => void
  onEnglishNameChange: (value: string) => void
  onOpenChange: (open: boolean) => void
  onRemoveFolder: (path: string) => void
  onRetranslate: () => void
  onSubmit: () => void
  open: boolean
  paths: string[]
  preview?: ReactNode
  retranslateLabel: string
  showFolders?: boolean
  submitLabel: string
  title: string
  translating: boolean
}) {
  return (
    <Dialog
      onOpenChange={value => {
        // 正在落库时不接受 Esc / 遮罩关闭：半截状态比不让关更糟。
        if (!value && busy) {
          return
        }

        onOpenChange(value)
      }}
      open={open}
    >
      <DialogContent className="max-w-md" onInteractOutside={event => busy && event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>

        <form
          className="grid gap-4"
          onSubmit={event => {
            event.preventDefault()

            if (!busy && canSubmit) {
              onSubmit()
            }
          }}
        >
          <AgentNameFields
            autoFocus
            displayName={displayName}
            englishHelp={englishHelp}
            englishLabel={englishLabel}
            englishName={englishName}
            idPrefix={idPrefix}
            nameLabel={nameLabel}
            onDisplayNameChange={onDisplayNameChange}
            onEnglishNameChange={onEnglishNameChange}
            onRetranslate={onRetranslate}
            retranslateLabel={retranslateLabel}
            showEnglishHelp={!isValidEnglishName(englishName)}
            translating={translating}
          />

          {extra}

          {showFolders && (
            <AgentFolderFields
              addLabel={folderLabels.add}
              emptyText={folderLabels.empty}
              hint={folderLabels.hint}
              label={folderLabels.label}
              onAdd={onAddFolder}
              onRemove={onRemoveFolder}
              paths={paths}
              primaryLabel={folderLabels.primary}
              removeLabel={folderLabels.remove}
            />
          )}

          <AgentAppearanceFields
            clearLabel={appearanceLabels.clear}
            color={color}
            hint={appearanceLabels.hint}
            icon={icon}
            label={appearanceLabels.label}
            onChangeColor={onChangeColor}
            onChangeIcon={onChangeIcon}
            onClear={onClearAppearance}
            preview={preview}
          />

          {error && <p className="text-xs text-destructive">{error}</p>}

          <DialogFooter>
            <Button disabled={busy} onClick={onCancel} type="button" variant="ghost">
              {cancelLabel}
            </Button>
            <Button disabled={busy || !canSubmit} type="submit">
              {busy ? busyLabel : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

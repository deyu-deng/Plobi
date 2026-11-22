import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

import { AGENT_COLOR_CHOICES, AGENT_ICON_CHOICES } from './create-agent'

/**
 * 「新建 Agent」与「编辑这个 Agent」共用的三块表单。
 *
 * 用户 2026-11-22：「编辑这个 Agent 用的组件和新建用的组件不一样呀。没有复用。
 * 这不对吧。」——以前两份各写一遍，于是图标网格的边框、颜色的选中环、工作目录
 * 主文件夹的标法全在两个文件里各自演化。现在只有这一份，两边只是喂不同的 state。
 */

/** 名字：中文名（左栏那行字）+ 英文名（英文界面那行字，也是 agent_id 的来源）。 */
export function AgentNameFields({
  autoFocus = false,
  children,
  displayName,
  englishHelp,
  englishLabel,
  englishName,
  idPrefix,
  nameLabel,
  onDisplayNameChange,
  onEnglishNameChange,
  onRetranslate,
  retranslateLabel,
  showEnglishHelp = true,
  translating
}: {
  autoFocus?: boolean
  children?: ReactNode
  displayName: string
  englishHelp: string
  englishLabel: string
  englishName: string
  idPrefix: string
  nameLabel: string
  onDisplayNameChange: (value: string) => void
  onEnglishNameChange: (value: string) => void
  onRetranslate: () => void
  retranslateLabel: string
  showEnglishHelp?: boolean
  translating: boolean
}) {
  return (
    <>
      <div className="grid gap-1.5">
        <label className="text-xs font-medium" htmlFor={`${idPrefix}-name`}>
          {nameLabel}
        </label>
        <Input
          autoFocus={autoFocus}
          id={`${idPrefix}-name`}
          onChange={event => onDisplayNameChange(event.target.value)}
          value={displayName}
        />
      </div>

      <div className="grid gap-1.5">
        <label className="text-xs font-medium" htmlFor={`${idPrefix}-english`}>
          {englishLabel}
        </label>
        <div className="flex items-center gap-1.5">
          <Input
            id={`${idPrefix}-english`}
            onChange={event => onEnglishNameChange(event.target.value)}
            value={englishName}
          />
          {/* 翻得不对就点这一下重翻（用户 2026-11-22：要一枚刷新的矢量按钮，不要文字链）。 */}
          <Button
            aria-label={retranslateLabel}
            disabled={translating || !displayName.trim()}
            onClick={onRetranslate}
            size="icon"
            title={retranslateLabel}
            type="button"
            variant="ghost"
          >
            <Codicon name="refresh" spinning={translating} />
          </Button>
        </div>
        {/* 门牌（agent_id）不摆在界面上：那是文件系统与路由的事，用户只需要名字。
            用户 2026-11-22：「界面也有很多没必要展示给用户的元注释，该删的删」。 */}
        {showEnglishHelp && <p className="text-xs text-muted-foreground">{englishHelp}</p>}
      </div>

      {children}
    </>
  )
}

/** 图标与颜色（创建时选过、左栏以前从不渲染，现在两边一致）。 */
export function AgentAppearanceFields({
  clearLabel,
  color,
  hint,
  icon,
  label,
  onChangeColor,
  onChangeIcon,
  onClear,
  preview
}: {
  clearLabel: string
  color: string
  hint?: string
  icon: string
  label: string
  onChangeColor: (value: string) => void
  onChangeIcon: (value: string) => void
  onClear: () => void
  preview?: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{label}</span>
      <div className="flex flex-wrap items-center gap-1">
        {AGENT_ICON_CHOICES.map(name => (
          <Button
            aria-label={name}
            aria-pressed={icon === name}
            className={cn(
              'size-7',
              icon === name
                ? 'bg-(--ui-control-hover-background) text-foreground'
                : 'text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground'
            )}
            key={name}
            onClick={() => onChangeIcon(icon === name ? '' : name)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name={name} size="0.875rem" />
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {AGENT_COLOR_CHOICES.map(choice => (
          <button
            aria-label={choice}
            aria-pressed={color === choice}
            className={cn(
              'size-5 rounded-full ring-offset-2 transition-shadow',
              color === choice
                ? 'ring-2 ring-(--ui-text-tertiary) ring-offset-background'
                : 'hover:ring-2 hover:ring-(--ui-text-quaternary) hover:ring-offset-background'
            )}
            key={choice}
            onClick={() => onChangeColor(color === choice ? '' : choice)}
            style={{ backgroundColor: choice }}
            type="button"
          />
        ))}
        <Button
          aria-label={clearLabel}
          className="size-7 text-(--ui-text-quaternary) hover:text-foreground"
          onClick={onClear}
          size="icon-xs"
          title={clearLabel}
          type="button"
          variant="ghost"
        >
          <Codicon name="close" size="0.75rem" />
        </Button>
      </div>
      {preview}
      {hint && <p className="text-[0.6875rem] text-(--ui-text-tertiary)">{hint}</p>}
    </div>
  )
}

/** 工作目录清单：主文件夹在前（后端取首个驱动右栏挂载）。 */
export function AgentFolderFields({
  addLabel,
  emptyText,
  hint,
  label,
  onAdd,
  onRemove,
  paths,
  primaryLabel,
  removeLabel
}: {
  addLabel: string
  emptyText: string
  hint?: string
  label: string
  onAdd: () => void
  onRemove: (path: string) => void
  paths: string[]
  primaryLabel: string
  removeLabel: string
}) {
  return (
    <div className="grid gap-1.5">
      <span className="text-[0.6875rem] font-medium text-(--ui-text-tertiary)">{label}</span>
      {paths.length === 0
        ? <p className="text-[0.75rem] text-(--ui-text-tertiary)">{emptyText}</p>
        : (
            <ul className="grid gap-1">
              {paths.map((path, index) => (
                <li
                  className="flex items-center gap-1.5 rounded-md border border-(--ui-stroke-tertiary) px-2 py-1 text-xs"
                  key={path}
                >
                  <Codicon
                    className="shrink-0 text-(--ui-text-tertiary)"
                    name={index === 0 ? 'folder-active' : 'folder'}
                    size="0.75rem"
                  />
                  <span className="min-w-0 flex-1 truncate" title={path}>
                    {path}
                  </span>
                  {index === 0 && (
                    <span className="shrink-0 text-[0.625rem] text-(--ui-text-quaternary)">{primaryLabel}</span>
                  )}
                  <Button
                    aria-label={removeLabel}
                    className="size-5 shrink-0 text-(--ui-text-tertiary) hover:text-foreground"
                    onClick={() => onRemove(path)}
                    size="icon-xs"
                    type="button"
                    variant="ghost"
                  >
                    <Codicon name="close" size="0.75rem" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
      <Button aria-label={addLabel} className="self-start" onClick={onAdd} size="sm" type="button" variant="ghost">
        <Codicon name="add" size="0.75rem" />
        {addLabel}
      </Button>
      {hint && <p className="text-[0.6875rem] text-(--ui-text-tertiary)">{hint}</p>}
    </div>
  )
}

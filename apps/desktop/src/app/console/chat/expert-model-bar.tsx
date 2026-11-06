import { useCallback, useEffect, useMemo, useState } from 'react'

import { getExpertRoles, setExpertRoleModel } from '@/app/chat/sidebar/expert-runs'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { getGlobalModelOptions } from '@/plobi'
import type { ModelOptionProvider } from '@/types/plobi'

/**
 * 专家卡片底部那条模型选择（用户 11-04）。
 *
 * 「专家的也应该有模型选择框。可以使用和 L2 Agent 不同的模型」——所以这条挂在
 * **角色**上，不是挂在某一次会话上：选完存进 `experts/roles.json`，这个角色以后
 * 每次出场都用它。清空就是回到继承父 Agent（与普通子代理同一条默认）。
 *
 * 位置与样式照主对话底部那颗胶囊（同一套 `--composer-control-size` 高度与字号），
 * 因为用户点名「和主区对话框一样放到底部一样的位置，组件样式一样」。
 */

const PILL = cn(
  'h-(--composer-control-size) max-w-48 shrink-0 gap-1 rounded-md px-2 text-xs font-normal',
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
)

export function ExpertModelBar({ role }: { role: string }) {
  const { t } = useI18n()
  const c = t.console
  const [model, setModel] = useState<null | string>(null)
  const [providers, setProviders] = useState<ModelOptionProvider[]>([])
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      const body = await getExpertRoles()
      const row = (body?.data?.roles ?? []).find(item => item.key === role)

      setModel(row?.model ?? null)
    } catch {
      // 读不到就当没配：显示「跟随 L2」，不让这一条把卡片打红。
      setModel(null)
    }
  }, [role])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    getGlobalModelOptions()
      .then(body => setProviders(body?.providers ?? []))
      .catch(() => setProviders([]))
  }, [])

  const pick = async (value: string) => {
    if (saving) {
      return
    }

    setSaving(true)

    try {
      const body = await setExpertRoleModel(role, value)

      setModel(body?.data?.model ?? null)
      setOpen(false)
    } catch {
      // 写失败时保留原来的显示：屏幕上的字不能先跳了再退回去。
    } finally {
      setSaving(false)
    }
  }

  // 只列真的有模型的 provider；空目录时不画下拉（一颗点了没东西的按钮没有意义）。
  const rows = useMemo(
    () => providers.filter(provider => (provider.models ?? []).length > 0),
    [providers]
  )

  const label = model ? model.split('/').pop() || model : c.expertModelFollow
  const title = model ? `${c.expertModelHint} · ${model}` : c.expertModelHint

  if (rows.length === 0) {
    return (
      <Button className={PILL} disabled type="button" variant="ghost">
        <span className="truncate">{label}</span>
      </Button>
    )
  }

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      <DropdownMenuTrigger asChild>
        <Button aria-label={title} className={PILL} disabled={saving} title={title} type="button" variant="ghost">
          <span className="truncate">{label}</span>
          <Codicon className="opacity-50" name="chevron-down" size="0.625rem" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-80 w-64 overflow-y-auto p-1" side="top" sideOffset={8}>
        <DropdownMenuItem className="text-xs" onSelect={() => void pick('')}>
          <span className="flex-1 truncate">{c.expertModelFollow}</span>
          {model ? null : <Codicon name="check" size="0.75rem" />}
        </DropdownMenuItem>
        {rows.map(provider => (
          <div key={provider.slug}>
            <p className="px-2 pt-2 pb-1 text-[0.65rem] font-medium tracking-wide text-(--ui-text-quaternary) uppercase">
              {provider.name || provider.slug}
            </p>
            {(provider.models ?? []).map(name => {
              const qualified = `${provider.slug}/${name}`

              return (
                <DropdownMenuItem className="text-xs" key={qualified} onSelect={() => void pick(qualified)}>
                  <span className="flex-1 truncate">{name}</span>
                  {model === qualified ? <Codicon name="check" size="0.75rem" /> : null}
                </DropdownMenuItem>
              )
            })}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

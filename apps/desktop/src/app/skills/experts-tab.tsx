import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import {
  type ExpertCapability,
  type ExpertRoleInfo,
  type ExpertRolesResponse,
  getExpertRoles,
  setExpertRoleDisabledCapabilities,
  setExpertRoleModel
} from '@/app/chat/sidebar/expert-runs'
import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n'
import { displayModelName, modelDisplayParts, qualifyModelId } from '@/lib/model-status-label'
import { writeCache } from '@/lib/query-client'
import { cn } from '@/lib/utils'
import { getGlobalModelOptions } from '@/plobi'
import { notify, notifyError } from '@/store/notifications'
import type { ModelOptionProvider } from '@/types/plobi'

import { ListStrip, ListStripMenu } from '../master-detail'
import { PanelEmpty } from '../overlays/panel'

/**
 * 「名手」那一格（Capabilities 屏的第五个子 tab）。
 *
 * 名手是**一类角色**（文件／浏览器／软件／电脑），不是名册里的某个 Agent ⇒ 这一格
 * **不跟 scope 走**（全局／这个 Agent 的切换对它不适用），读写的都是角色级真源。
 *
 * 每行：角色的字标 ＋ 名字 ＋ 它默认有的那几项能力（各一枚开关）＋ 它自己的默认模型。
 *
 * ⚠ **开关的语义**：后端只认「黑名单」（`disabled_capabilities`），**只会减，不会加**
 * ——池子里这一类默认没有的能力项（例：「看图」）勾上也存不进去。所以那一格**只画这一类
 * 默认就有的能力项**，开关＝「默认开着」还是「进了它自己的黑名单」。
 */

const EXPERTS_ROLES_KEY = ['experts-roles'] as const

const setRoles = writeCache<ExpertRolesResponse>(EXPERTS_ROLES_KEY)

/** 后端把「不认得的能力项」那句放在 body 的 `error` 里，桥把它塞进 Error 的 message。
 *  这里把那一句掏出来给人看——别把整段 JSON 端上屏。 */
function backendErrorText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? '')
  const match = raw.match(/"error"\s*:\s*"([^"]+)"/)

  return match?.[1] ?? raw
}

/** 这一类名手**默认**有的能力项（人话标签 → 能力项键）。
 *
 * 键从能力池反查（池子里是键 ＋ 标签，角色那几行只给标签）。反查不到的那一项
 * 画不出来——没有键就写不回去。
 */
function defaultCapabilities(role: ExpertRoleInfo, pool: ExpertCapability[]): ExpertCapability[] {
  const keyOf = new Map(pool.map(item => [item.label, item.key]))

  return role.capabilities
    .map(label => {
      const key = keyOf.get(label)

      return key ? { key, label } : null
    })
    .filter((item): item is ExpertCapability => item !== null)
}

export function ExpertsTab() {
  const { t } = useI18n()
  const e = t.skills.experts

  const { data, error, isError, isLoading, refetch } = useQuery({
    queryKey: EXPERTS_ROLES_KEY,
    queryFn: getExpertRoles,
    staleTime: 0
  })

  const roles = useMemo(() => data?.data?.roles ?? [], [data])
  const pool = useMemo(() => data?.data?.capabilities ?? [], [data])
  // 写口是「读—改—写」：同一类的两次写撞在一起会互相覆盖 ⇒ 一次只放一笔。
  const [busy, setBusy] = useState(false)

  /** 乐观写穿：先把这一行改掉，回头后端读数再把它对回去。 */
  const patchRole = (roleKey: string, patch: Partial<ExpertRoleInfo>) => {
    setRoles(current => {
      if (!current) {
        return current
      }

      return {
        ...current,
        data: {
          ...current.data,
          roles: (current.data?.roles ?? []).map(row => (row.key === roleKey ? { ...row, ...patch } : row))
        }
      }
    })
  }

  const toggleCapability = async (role: ExpertRoleInfo, capability: ExpertCapability, enabled: boolean) => {
    if (busy) {
      return
    }

    const next = new Set(role.disabled_capabilities)

    if (enabled) {
      next.delete(capability.key)
    } else {
      next.add(capability.key)
    }

    const disabled = [...next]
    const before = role.disabled_capabilities

    patchRole(role.key, { disabled_capabilities: disabled })
    setBusy(true)

    try {
      await setExpertRoleDisabledCapabilities(role.key, disabled)
    } catch (err) {
      patchRole(role.key, { disabled_capabilities: before })
      notifyError(new Error(backendErrorText(err)), t.skills.failedToUpdate(role.label))
    } finally {
      setBusy(false)
    }
  }

  /** 这一类名手自己的默认模型。空串＝回到继承父 Agent（与普通子代理同一条默认）。 */
  const pickModel = async (role: ExpertRoleInfo, value: string) => {
    if (busy) {
      return false
    }

    const before = role.model
    const next = value.trim() ? value : null

    patchRole(role.key, { model: next })
    setBusy(true)

    try {
      await setExpertRoleModel(role.key, value)

      return true
    } catch (err) {
      patchRole(role.key, { model: before })
      notifyError(new Error(backendErrorText(err)), t.skills.failedToUpdate(role.label))

      return false
    } finally {
      setBusy(false)
    }
  }

  /** 整格一起开关：黑名单清空＝默认全开；全关＝这一类默认有的能力项全部进黑名单。 */
  const bulkToggle = async (enabled: boolean) => {
    if (busy || roles.length === 0) {
      return
    }

    setBusy(true)
    let done = 0

    try {
      for (const role of roles) {
        const disabled = enabled ? [] : defaultCapabilities(role, pool).map(capability => capability.key)

        await setExpertRoleDisabledCapabilities(role.key, disabled)
        patchRole(role.key, { disabled_capabilities: disabled })
        done += 1
      }

      notify({ kind: 'success', title: e.bulkUpdated(done), message: '' })
    } catch (err) {
      notifyError(new Error(backendErrorText(err)), t.skills.failedToUpdate(t.skills.tabExperts))
    } finally {
      setBusy(false)
    }
  }

  const allOn = roles.length > 0 && roles.every(role => role.disabled_capabilities.length === 0)

  if (isError) {
    return (
      <PanelEmpty
        action={
          <Button onClick={() => void refetch()} size="sm">
            {t.common.refresh}
          </Button>
        }
        description={error instanceof Error ? error.message : undefined}
        icon="error"
        title={e.loadFailed}
      />
    )
  }

  if (isLoading) {
    return <PageLoader label={t.skills.loading} />
  }

  if (roles.length === 0) {
    return <PanelEmpty icon="info" title={e.emptyTitle} />
  }

  return (
    <div className="h-full min-h-0 overflow-y-auto overscroll-contain px-2 py-2">
      <div className="mx-auto flex max-w-3xl flex-col">
        <ListStrip
          right={
            <ListStripMenu
              label={t.skills.tabExperts}
              toggle={{
                checked: allOn,
                disabled: busy,
                label: t.skills.all,
                onToggle: checked => void bulkToggle(checked)
              }}
            />
          }
        />
        {roles.map(role => (
          <ExpertRow
            busy={busy}
            key={role.key}
            onModel={value => pickModel(role, value)}
            onToggle={(capability, enabled) => void toggleCapability(role, capability, enabled)}
            pool={pool}
            role={role}
          />
        ))}
      </div>
    </div>
  )
}

function ExpertRow({
  busy,
  onModel,
  onToggle,
  pool,
  role
}: {
  busy: boolean
  onModel: (value: string) => Promise<boolean>
  onToggle: (capability: ExpertCapability, enabled: boolean) => void
  pool: ExpertCapability[]
  role: ExpertRoleInfo
}) {
  const { t } = useI18n()
  const e = t.skills.experts
  const capabilities = useMemo(() => defaultCapabilities(role, pool), [pool, role])
  const disabled = useMemo(() => new Set(role.disabled_capabilities), [role.disabled_capabilities])
  const on = capabilities.filter(capability => !disabled.has(capability.key)).length

  return (
    <div className="group/row row-hover flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-1.5 hover:text-foreground">
      <div className="flex min-w-0 shrink-0 items-center gap-2">
        <Badge className="size-5 justify-center p-0" variant="muted">
          {role.mark}
        </Badge>
        <span className="truncate text-[0.78rem] font-medium text-foreground/85">{role.label}</span>
        <span className="shrink-0 rounded bg-(--ui-bg-quinary) px-1 py-px text-[0.6rem] tabular-nums leading-3.5 text-(--ui-text-tertiary)">
          {on}/{capabilities.length}
        </span>
      </div>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
        {capabilities.map(capability => {
          const enabled = !disabled.has(capability.key)
          const label = e.toggleCapability(role.label, capability.label)

          return (
            <span className="flex items-center gap-1.5" key={capability.key}>
              <span
                className={cn(
                  'text-[0.68rem]',
                  enabled ? 'text-(--ui-text-secondary)' : 'text-muted-foreground/50 line-through'
                )}
              >
                {capability.label}
              </span>
              <Switch
                aria-label={label}
                checked={enabled}
                disabled={busy}
                onCheckedChange={next => onToggle(capability, next)}
                size="xs"
                title={label}
              />
            </span>
          )
        })}
      </div>
      <RoleModelPicker model={role.model} onPick={onModel} />
    </div>
  )
}

/**
 * 这一类名手自己的默认模型（那颗下拉胶囊）。
 *
 * 形状照 `app/console/chat/expert-model-bar.tsx`：同一套高度、字号与下拉交互。
 * 差别只有一处——**状态从这一格自己的名单读**，而不是自己再读一遍后端（那样两处会各说各话），
 * 写失败也会把后端那句原话报出来。
 */
const PILL = cn(
  'h-(--composer-control-size) max-w-48 shrink-0 gap-1 rounded-md px-2 text-xs font-normal',
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
)

function RoleModelPicker({
  model,
  onPick
}: {
  model: null | string
  onPick: (value: string) => Promise<boolean>
}) {
  const { t } = useI18n()
  const c = t.console
  const [providers, setProviders] = useState<ModelOptionProvider[]>([])
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    getGlobalModelOptions()
      .then(body => setProviders(body?.providers ?? []))
      .catch(() => setProviders([]))
  }, [])

  // 只列真的有模型的 provider；空目录时不画下拉（一颗点了没东西的按钮没有意义）。
  const rows = useMemo(() => providers.filter(provider => (provider.models ?? []).length > 0), [providers])

  const pick = async (value: string) => {
    if (saving) {
      return
    }

    setSaving(true)

    try {
      if (await onPick(value)) {
        setOpen(false)
      }
    } finally {
      setSaving(false)
    }
  }

  // `model` is stored qualified (`workbuddy/auto`), so the pill must not print the raw
  // id — `displayModelName` strips the provider prefix and prettifies the rest, which
  // is what the composer already does. Before this, `workbuddy/auto` leaked onto the
  // pill as-is and read as a machine path.
  const label = model ? displayModelName(model) : c.expertModelFollow
  const title = model ? `${c.expertModelHint} · ${displayModelName(model)}` : c.expertModelHint

  if (rows.length === 0) {
    return (
      <Button className={PILL} disabled title={c.expertModelEmpty} type="button" variant="ghost">
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
        {rows.map(provider => {
          // `provider.name` is the human row title, `slug` is what the backend speaks.
          // The composer already groups this way; the 名手 picker used to flatten every
          // provider into one undifferentiated list of bare names.
          const selected = model ? qualifyModelId(provider.slug, model) : ''

          return (
            <DropdownMenuGroup key={provider.ui_key || provider.slug || provider.name}>
              <DropdownMenuLabel className="text-[0.6875rem] font-normal text-(--ui-text-tertiary)">
                {provider.name || provider.slug}
              </DropdownMenuLabel>
              {(provider.models ?? []).map(raw => {
                // Catalogs differ: hub software ships `workbuddy/auto`, plain providers
                // ship `auto`. `qualifyModelId` is idempotent, so the item value is
                // always exactly one prefix deep and `pick` writes a resolvable id
                // (this line used to concatenate blindly and persisted
                // `workbuddy/workbuddy/auto` into the role config).
                const qualified = qualifyModelId(provider.slug, raw)
                const parts = modelDisplayParts(qualified)

                return (
                  <DropdownMenuItem className="text-xs" key={qualified} onSelect={() => void pick(qualified)}>
                    <span className="flex-1 truncate">{parts.name}</span>
                    {parts.tag ? (
                      <span className="shrink-0 text-[0.625rem] text-(--ui-text-tertiary)">{parts.tag}</span>
                    ) : null}
                    {selected === qualified ? <Codicon name="check" size="0.75rem" /> : null}
                  </DropdownMenuItem>
                )
              })}
            </DropdownMenuGroup>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

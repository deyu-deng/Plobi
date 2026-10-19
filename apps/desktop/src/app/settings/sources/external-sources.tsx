/**
 * 「接一个自己的」那一格：装 / 卸一个第三方信源包。
 *
 * 这是产品面上**唯一一个会执行任意第三方代码**的地方，所以界面上有三条硬约束，
 * 三条都在下面写成代码而不是注释：
 *
 * 1. **按钮上写清楚它会执行代码。** 不是免责声明，是事实：一个 pip 包在装的时候
 *    就会跑 setup。纪律「不许描述不存在的机制」在这里的反面就是「不许把它说得
 *    比实际更轻」。
 * 2. **装���不是零成本查询。** 回的是动作名，日志尾巴一行行出来——所以这一格
 *    必须显示"正在装"，不能转个圈就完事。
 * 3. **只收包名。** 输入框把形状限制在「一个词」：不接受版本约束、URL、路径。
 *    这不是这里在替后端拦（后端 `validate_package_name` 才是权威），而是让人
 *    **在按下去之前**就知道什么能按。真的收窄了按下去之后那句话给谁看。
 *
 * 不做的：版本、extras、git 地址、要密码的私源。要那些请去终端 —— 那里有 shell、
 * 有权限感、有人看着。
 */

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getActionStatus } from '@/plobi'
import { notify, notifyError } from '@/store/notifications'

import type { ExternalPackage } from './api'
import { getExternalPackages, installExternalPackage, removeExternalPackage } from './api'

type Labels = {
  emptyExternal: string
  externalLead: string
  install: string
  installFailed: string
  installPlaceholder: string
  installsForeignCode: string
  installedNone: string
  loadingLog: string
  packageName: string
  remove: string
  removeFailed: string
  uninstalled: string
  uninstalledDone: string
}

const POLL_MS = 1200

export function ExternalSources({ labels, onChanged }: { labels: Labels; onChanged: () => Promise<void> }) {
  const [packages, setPackages] = useState<ExternalPackage[] | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [lines, setLines] = useState<string[] | null>(null)
  const [failed, setFailed] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true

    return () => {
      alive.current = false
    }
  }, [])

  const reload = async () => {
    try {
      const rows = await getExternalPackages()

      if (alive.current) {
        setPackages(rows)
        setFailed(false)
      }
    } catch {
      if (alive.current) {
        setFailed(true)
      }
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  // 一个字面上「合法的形状」也不等于后端会收（后端还拒版本约束 / extras / URL）。
  // 所以这里**不拦**提交——那会让人以为「我能填的都行」。真正那句话由后端给，
  // 原样显示在日志里。
  const run = async (action: 'install' | 'remove', packageName: string) => {
    setBusy(true)
    setLines([])

    try {
      const actionName = action === 'install'
        ? await installExternalPackage(packageName)
        : await removeExternalPackage(packageName)

      let exitCode: null | number = null

      for (;;) {
        const status = await getActionStatus(actionName, 200)

        if (!alive.current) {
          return
        }

        setLines(status.lines ?? [])

        if (!status.running) {
          exitCode = status.exit_code

          break
        }

        await new Promise(resolve => setTimeout(resolve, POLL_MS))
      }

      await reload()
      await onChanged()

      // 只有干净退出才说成功。失败时后端/CLI 写的那句话已经在日志里了 ——
      // 这里不再转述一遍，重复一遍只会让人以为是两件不同的事。
      if (exitCode === 0) {
        notify({ kind: 'success', message: action === 'install' ? labels.uninstalledDone : labels.uninstalled })
      } else {
        notify({ kind: 'error', message: action === 'install' ? labels.installFailed : labels.removeFailed })
      }
    } catch (err) {
      notifyError(err, action === 'install' ? labels.installFailed : labels.removeFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-2 space-y-3" data-testid="external-sources">
      <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{labels.externalLead}</p>

      <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{labels.installsForeignCode}</p>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label={labels.packageName}
          className="min-w-0 flex-1"
          disabled={busy}
          onChange={event => setName(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !busy && name.trim()) {
              void run('install', name.trim())
            }
          }}
          placeholder={labels.installPlaceholder}
          type="text"
          value={name}
        />
        <Button
          disabled={busy || !name.trim()}
          onClick={() => { void run('install', name.trim()) }}
          size="sm"
          variant="secondary"
        >
          {labels.install}
        </Button>
      </div>

      {failed ? <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{labels.emptyExternal}</p> : null}

      {!failed && packages?.length === 0 ? (
        <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">{labels.installedNone}</p>
      ) : null}

      {packages?.map(pkg => (
        <div className="flex items-center justify-between gap-2" key={pkg.entry_point}>
          <span className="min-w-0 truncate text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary)">
            {pkg.source ?? pkg.load_error ?? pkg.entry_point}
          </span>
          <Button
            disabled={busy}
            onClick={() => { void run('remove', pkg.distribution) }}
            size="sm"
            variant="textStrong"
          >
            {labels.remove}
          </Button>
        </div>
      ))}

      {lines?.length ? (
        <pre
          className="max-h-48 overflow-auto rounded-md bg-(--ui-surface-secondary) p-2 text-[0.72rem] text-(--ui-text-tertiary)"
          data-testid="external-log"
        >
          {lines.join('\n')}
        </pre>
      ) : null}

      {!lines?.length && busy ? (
        <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)" data-testid="external-log-empty">
          {labels.loadingLog}
        </p>
      ) : null}
    </div>
  )
}

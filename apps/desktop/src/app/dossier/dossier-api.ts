import { unwrapEnvelope } from '@/lib/api-envelope'

/**
 * 档案「进度」那一份按日清单的读侧。
 *
 * 真源是档案目录下的 `tasks.json`（与 `plan.md`／`progress.md` 同目录）——**不在库里**：
 * 人（这里）在屏上勾，后端改写那个文件；Agent 用 `dossier_write` 改**同一个**文件。
 * 两边共用一处真源 ⇒ 谁标的都能看见。
 *
 * 这层只吐 JSON 键与值：屏上文案由前端翻（屏幕文案纪律：机器词不许上屏）。
 */

const TIMEOUT_MS = 20_000

export interface DossierTaskItem {
  id: string
  title: string
  done: boolean
  doneBy: string
  updatedAt: string
}

export interface DossierTaskDay {
  date: string
  items: DossierTaskItem[]
}

export interface DossierTasks {
  /** 档案里到底有没有这份清单（没有 ≠ 空，是「还没建」）。 */
  exists: boolean
  /** 读不出来时的原因（前端据此说「读不到」，不许说成「一个都没有」）。 */
  error: string | null
  days: DossierTaskDay[]
}

export async function fetchDossierTasks(agentId: string): Promise<DossierTasks> {
  const payload = await window.plobiDesktop.api<unknown>({
    path: `/api/surface/agents/${encodeURIComponent(agentId)}/tasks`,
    timeoutMs: TIMEOUT_MS
  })

  return unwrapEnvelope<DossierTasks>(payload, 'surface')
}

/** 勾一条／取消勾一条。`done` 是**目标状态**，不是翻转（翻转在并发下会打架）。 */
export async function setDossierTaskDone(
  agentId: string,
  date: string,
  id: string,
  done: boolean
): Promise<DossierTasks> {
  const payload = await window.plobiDesktop.api<unknown>({
    body: { date, done, id },
    method: 'POST',
    path: `/api/surface/agents/${encodeURIComponent(agentId)}/tasks/toggle`,
    timeoutMs: TIMEOUT_MS
  })

  return unwrapEnvelope<DossierTasks>(payload, 'surface')
}

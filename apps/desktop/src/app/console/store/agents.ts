import { atom, computed } from 'nanostores'

import { fetchAgents } from '../api'
import type { Agent } from '../types'

/**
 * Console data atoms (U1: agent rail).
 *
 * Mirrors the agenda store's contract: the backend owns the truth, these atoms
 * are a render cache, and anything that mutates writes the server's answer back
 * rather than patching optimistically.
 */

export const $consoleAgents = atom<Agent[]>([])
export const $consoleAgentsLoading = atom(true)
export const $consoleAgentsError = atom<null | string>(null)

/** Agents the user has to unblock — drives the rail's attention hint. */
export const $consoleBlockedAgents = computed($consoleAgents, agents =>
  agents.filter(agent => agent.status === 'awaiting_approval' || agent.status === 'error')
)

export const $consoleBlockedCount = computed($consoleBlockedAgents, blocked => blocked.length)

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

export function setConsoleAgents(agents: Agent[]): void {
  $consoleAgents.set(agents)
}

export function setConsoleAgentsError(message: null | string): void {
  $consoleAgentsError.set(message)
}

export function setConsoleAgentsLoading(loading: boolean): void {
  $consoleAgentsLoading.set(loading)
}

/**
 * 一次成功读取就把自动重试的预算还回去；失败则最多再试两次（2s、5s）。
 * 为什么要有这一条：左栏只在挂载时拉一次名册，而后端会在改配置的那一瞬间重启/换端口——
 * 那次拉失败过就再也没人补，用户看到的是一栏空白直到重开应用（2026-10-26 现场）。
 * 上限两次是故意的：后端真死了的时候不许把请求打成热循环。
 */
const AUTO_RETRY_DELAYS_MS = [2_000, 5_000]

let autoRetriesLeft = AUTO_RETRY_DELAYS_MS.length
let retryTimer: ReturnType<typeof setTimeout> | null = null

export async function refreshConsoleAgents(): Promise<void> {
  setConsoleAgentsLoading(true)

  try {
    const agents = await fetchAgents()

    setConsoleAgents(agents)
    setConsoleAgentsError(null)
    autoRetriesLeft = AUTO_RETRY_DELAYS_MS.length
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
  } catch (cause) {
    // 关键：失败时**不动** $consoleAgents（留着上一次读到的那一栏），只把错误位立起来，
    // 让界面能说「没读到」而不是「没有 Agent」。
    setConsoleAgentsError(messageOf(cause))
    if (autoRetriesLeft > 0) {
      const delay = AUTO_RETRY_DELAYS_MS[AUTO_RETRY_DELAYS_MS.length - autoRetriesLeft]

      autoRetriesLeft -= 1
      if (retryTimer) {
        clearTimeout(retryTimer)
      }
      retryTimer = setTimeout(() => {
        retryTimer = null
        void refreshConsoleAgents()
      }, delay)
    }
  } finally {
    setConsoleAgentsLoading(false)
  }
}

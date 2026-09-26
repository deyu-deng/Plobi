import { useQuery } from '@tanstack/react-query'

import { getPlobiConfigRecord } from '@/plobi'
import { queryClient, writeCache } from '@/lib/query-client'
import type { PlobiConfigRecord } from '@/types/plobi'

// One shared cache for the whole profile config record (`GET /api/config`).
// Every settings surface (MCP, model, config) reads and writes through this key
// so a save in one shows in the others, and revisiting a tab paints the cache
// instead of blanking on a fresh fetch.
//
// Distinct from session/hooks/use-plobi-config.ts, which is side-effecting —
// it pushes personality/cwd/voice/… into the session stores for live chat.
export const PLOBI_CONFIG_KEY = ['plobi-config-record'] as const

// staleTime 0 → serve cache instantly, background-revalidate on every mount.
export const usePlobiConfigRecord = () =>
  useQuery({ queryKey: PLOBI_CONFIG_KEY, queryFn: getPlobiConfigRecord, staleTime: 0 })

export const setPlobiConfigCache = writeCache<PlobiConfigRecord>(PLOBI_CONFIG_KEY)

export const invalidatePlobiConfig = () => queryClient.invalidateQueries({ queryKey: PLOBI_CONFIG_KEY })

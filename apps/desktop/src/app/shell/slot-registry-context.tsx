import { useQuery } from '@tanstack/react-query'
import { createContext, useContext, useMemo, type ReactNode } from 'react'

import { usePlobiConfigRecord } from '@/app/hooks/use-config-record'
import { getPlobiSlots } from '@/plobi'

import { createSlotRegistry, readRemovedSlots, type SlotRegistry } from './slot-registry'

/**
 * **全应用一册 slot**（界面外壳的挂载点——裁定 239）。
 *
 * 为什么要有这一层：slot 的真源只有三样东西——**出厂那份声明**、**插件填进来的东西**、
 * **用户关掉的那些**。这三样都是应用级的（拉一次就够），所以**只该有一册**：
 *
 * - 侧边栏那几格（第二刀）、工作区那几页签（第三刀）、对话卡那三枚（第四刀）
 *   都从**这一册**读；
 * - 之前那两处各自 `useQuery` 了一遍 `/api/slots`、各自读了一遍配置 ⇒ 两份注册表
 *   可能**不同步**（插件那份清单变了，左栏知道、工作区不知道）。
 *
 * **没有 Provider 时用出厂那一份**（`DEFAULT_REGISTRY`）：局部树与测试照旧能渲染，
 * 不用到处套 Provider；屏上装了什么插件是 Provider 那层的事。
 */
const DEFAULT_REGISTRY: SlotRegistry = createSlotRegistry()

const SlotRegistryContext = createContext<SlotRegistry>(DEFAULT_REGISTRY)

/**
 * 应用根部挂的那一个（`bootstrap-app.tsx`）。
 *
 * 取数两路：**插件填进来的**（`GET /api/slots`，取不到就照常用出厂那份——
 * 别让一次失败拖慢整屏）与**用户关掉的**（配置里的 `slots.removed`）。
 */
export function SlotRegistryProvider({ children }: { children: ReactNode }) {
  const { data: configRecord } = usePlobiConfigRecord()
  const { data: slotData } = useQuery({
    queryKey: ['plobi-slots'],
    queryFn: getPlobiSlots,
    staleTime: 5 * 60 * 1000,
    // 插件那份清单取不到时**不重试**：照常用出厂声明表渲染。
    retry: false
  })

  const registry = useMemo(
    () =>
      createSlotRegistry({
        removed: readRemovedSlots(configRecord),
        contributions: slotData?.contributions
      }),
    [configRecord, slotData]
  )

  return <SlotRegistryContext.Provider value={registry}>{children}</SlotRegistryContext.Provider>
}

/** 这一册 slot（没有 Provider 就是出厂那一份）。 */
export function useSlotRegistry(): SlotRegistry {
  return useContext(SlotRegistryContext)
}

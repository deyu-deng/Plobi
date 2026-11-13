import { atom } from 'nanostores'

/**
 * 忙时语义（**底座那一个键**）：`display.busy_input_mode`。
 *
 * 底座有三套同款语义，写在一处：
 * - CLI（`cli.py:3727-3736`）：Enter 在忙时干什么；
 * - 网关（`gateway/run.py:4939-4949`、`tui_gateway/server.py:415-420`）：从
 *   `config.yaml` 的 `display.busy_input_mode` 读，缺省 `interrupt`；
 * - 桌面端以前**自己**发明了一套（忙就排队），于是同一个产品两台机器两套脾气，
 *   而且桌面端那套还会跟底座顶牛（本地忙闸把消息拦在半路，底座压根没见到它）。
 *
 * 桌面端现在**只读这一个键**，三态：
 * - `interrupt`（底座缺省）：Enter 打断当前这一轮，这句话作为**下一轮**发出去；
 * - `queue`：Enter 排队，等这一轮结束再发（桌面端自己的可见队列）；
 * - `steer`：Enter 转向当前这一轮（`session.steer`，下一个工具调用后到达）；
 *   底座那边被拒时会回落到 `queue`。
 *
 * 用户在设置屏就能改（配置 schema 里 `display.busy_input_mode` 是个下拉，
 * `plobi_cli/web_server.py:892`），所以这里只管**读**。
 */
export type BusyInputMode = 'interrupt' | 'queue' | 'steer'

/** 底座的缺省（`tui_gateway/server.py:415-420` 同款）：`interrupt`。 */
export const DEFAULT_BUSY_INPUT_MODE: BusyInputMode = 'interrupt'

export const $busyInputMode = atom<BusyInputMode>(DEFAULT_BUSY_INPUT_MODE)

/** 认这个键的规矩跟底座逐字相同：认不出来的值当缺省。 */
export function normalizeBusyInputMode(value: unknown): BusyInputMode {
  const raw = typeof value === 'string' ? value.trim().toLowerCase() : ''

  if (raw === 'queue' || raw === 'steer' || raw === 'interrupt') {
    return raw
  }

  return DEFAULT_BUSY_INPUT_MODE
}

/** 当前生效的忙时语义。 */
export const getBusyInputMode = (): BusyInputMode => $busyInputMode.get()

export const setBusyInputMode = (mode: BusyInputMode) => $busyInputMode.set(mode)

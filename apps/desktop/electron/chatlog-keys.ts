/**
 * chatlog sidecar 的密钥交接 —— **密钥值一个字都不进台账、日志、命令行**。
 *
 * 为什么要这一档（现读上游，不是推测）：
 * - `chatlog server` 在 `internal/chatlog/manager.go:349` 就要求 `data_key`，拿不到就直接退出
 *   ⇒ 桌面此前那套"没有 key 也照样 spawn"永远起不来，5030 自然零监听。
 * - 上游把合并后的服务配置整包打进日志：`conf.go:93` 那句 `log.Info().Msgf("server config: %s", json)`
 *   里就含 `data_key`/`img_key` ⇒ **stdout 里带密钥**。桌面此前把子进程 stdout 逐行
 *   `rememberLog('[chatlog] …')`，等于把微信数据密钥抄进台账（裁定 110 一硬边界禁的那件事）。
 * - `chatlog key` 也是同一形状：`cmd_key.go:34` 的 `fmt.Println(ret)` 把密钥原样打到 stdout。
 *
 * 所以这里的规矩是三条：
 * 1. **我们不解析密钥 stdout**。让 chatlog 自己把它写进 `$CHATLOG_DIR/chatlog.json`
 *    （`UpdateConfig` 干的事），我们只读那个文件里白名单里的几个字段。
 * 2. `$CHATLOG_DIR` 钉在 Plobi 家目录下（D 盘），**不许落 `C:\Users\<user>\.chatlog`**（裁定 109 三）。
 * 3. 面上只登记"拿到 / 没拿到 + 指纹前 8 位"，指纹是 `sha256(key)` 的前缀 —— 它不可逆推密钥，
 *    够用来回答"这次到底取到没有、和上次是不是同一把"。
 */

import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const PROCESS_CONFIG_FILE = 'chatlog.json'
export const SERVER_CONFIG_FILE = 'chatlog-server.json'
export const SIDECAR_ADDR = '127.0.0.1:5030'

/**
 * chatlog 自己那两个配置文件里，我们只碰这几位。
 * 白名单而不是整包拷贝：`chatlog.json` 里还有 account / files 之类，抄进服务端配置没有意义，
 * 只是多一处会不一致的副本。
 */
export const HANDOFF_FIELDS = ['type', 'platform', 'version', 'data_dir', 'data_key', 'img_key'] as const

/**
 * 一份**扁平**结果。这里的 tsconfig 是 `strictNullChecks: false`，
 * 判别联合（`{ok:true}|{ok:false}`）在那种档下窄化不掉，调用方读 `detail` 会当场编译不过——
 * 所以失败原因与指纹都always带字段，用空串表示"这一格没有"。
 */
export type HandoffOutcome = {
  ok: boolean
  /** 失败原因码；成功时是 `'none'` */
  reason: 'none' | 'no-process-config' | 'no-key' | 'no-data-dir'
  /** 面上那句"为什么没到手"；成功时是那句"密钥在哪" */
  detail: string
  /** 密钥指纹前 8 位（不可逆推）；失败时空串 */
  fingerprint: string
  dataDir: string
  wroteServerConfig: boolean
}

const NO_KEY_FIELDS = { fingerprint: '', dataDir: '', wroteServerConfig: false } as const

export function chatlogConfigDir(plobiHome: string): string {
  return path.join(plobiHome, 'chatlog')
}

/** 不可逆推的一句读数：够说清"取到没取到、换没换过"，不够还原出密钥。 */
export function keyFingerprint(value: string): string {
  return crypto.createHash('sha256').update(value, 'utf8').digest('hex').slice(0, 8)
}

function readJsonFile(target: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(target, 'utf8'))
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

export function readProcessConfig(configDir: string): Record<string, unknown> | null {
  return readJsonFile(path.join(configDir, PROCESS_CONFIG_FILE))
}

/** 服务端配置已经是我们写过的那一份、且里面确有 data_key ⇒ 不必再取一次密钥。 */
export function serverConfigReady(configDir: string): boolean {
  const existing = readJsonFile(path.join(configDir, SERVER_CONFIG_FILE))
  return Boolean(existing && typeof existing.data_key === 'string' && (existing.data_key as string).length > 0)
}

/**
 * 把 chatlog 自己写的 `chatlog.json` 交接到 `chatlog-server.json`。
 *
 * 幂等：已经就绪就直接报就绪（每次开机都重取一遍密钥＝每次都去摸微信进程内存）。
 * 失败只报三种原因，不返空对象冒充成功 —— 面上那句得说得出为什么起不来。
 */
export function handoffServerConfig(configDir: string, workDir: string): HandoffOutcome {
  if (serverConfigReady(configDir)) {
    const ready = readJsonFile(path.join(configDir, SERVER_CONFIG_FILE)) as Record<string, unknown>
    return {
      ok: true,
      reason: 'none',
      detail: '已在 chatlog-server.json',
      fingerprint: keyFingerprint(String(ready.data_key)),
      dataDir: String(ready.data_dir || ''),
      wroteServerConfig: false
    }
  }

  const processConfig = readProcessConfig(configDir)
  if (!processConfig) {
    return {
      ok: false,
      reason: 'no-process-config',
      detail: `没有 ${PROCESS_CONFIG_FILE}（还没取过密钥）`,
      ...NO_KEY_FIELDS
    }
  }

  const dataKey = processConfig.data_key
  if (typeof dataKey !== 'string' || dataKey.length === 0) {
    return {
      ok: false,
      reason: 'no-key',
      detail: `${PROCESS_CONFIG_FILE} 里没有 data_key`,
      ...NO_KEY_FIELDS
    }
  }

  const dataDir = typeof processConfig.data_dir === 'string' ? processConfig.data_dir : ''
  if (dataDir.length === 0) {
    return {
      ok: false,
      reason: 'no-data-dir',
      detail: `${PROCESS_CONFIG_FILE} 里没有 data_dir`,
      ...NO_KEY_FIELDS
    }
  }

  const serverConfig: Record<string, unknown> = { http_addr: SIDECAR_ADDR, auto_decrypt: true, work_dir: workDir }
  for (const field of HANDOFF_FIELDS) {
    const value = processConfig[field]
    if (value !== undefined && value !== null && value !== '') {
      serverConfig[field] = value
    }
  }

  fs.mkdirSync(configDir, { recursive: true })
  const target = path.join(configDir, SERVER_CONFIG_FILE)
  const tmp = `${target}.tmp`
  try {
    fs.writeFileSync(tmp, `${JSON.stringify(serverConfig, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
    fs.renameSync(tmp, target)
  } catch (error) {
    try {
      fs.rmSync(tmp, { force: true })
    } catch {
      // 清理不动就行，失败下面照实报
    }
    throw error
  }

  return {
    ok: true,
    reason: 'none',
    detail: '刚写入 chatlog-server.json',
    fingerprint: keyFingerprint(dataKey),
    dataDir,
    wroteServerConfig: true
  }
}

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

/**
 * `chatlog key` 那一发的超时。上游一次**失败**的全地址空间扫描实测跑满 **167 秒**
 * （`Docs/rulings/architecture-rulings.md` 裁定 124 一），而旧值 90 秒会先把进程杀掉，
 * 于是我们永远只拿得到 Node 自己的 `ETIMEDOUT`，把上游那句真话（`no valid key found`）整条抹掉。
 * 这里给的是"跑完一趟全扫再加余量"，不是"更快放弃"。
 */
export const KEY_SCAN_TIMEOUT_MS = 240_000

/** 十六进制串一律遮掉：上游 stderr 里出现过 ≥16 位的十六进制段（`--debug` 那一档），而密钥就 32 字节。 */
const HEX_RUN = /[0-9a-fA-F]{12,}/g
/** 只取错误行；其余是进度噪音，进台账没意义还占地方。 */
const ERROR_LINE = /(error|err\b|fail|no valid key|not found|panic|fatal|denied|timeout)/i

/**
 * 把 `chatlog key` 那次失败压成一句**说真话**的话：上游自己那行错 + 退出码，不替它猜原因。
 *
 * 三条口径都是被现场教出来的：① 密钥在 **stdout**（`cmd_key.go:34` 那句 `Println(ret)`），
 * 所以调用侧必须 `stdio[1] = 'ignore'`，本函数**只看 stderr**；② stderr 里也可能有十六进制段，
 * 进台账前先遮；③ 旧文案那句「PC 微信在登录态吗」把三条已被实测排除的原因当唯一解释（裁定 124）。
 * 超时那一档只说"被我们杀了"，不假装拿到了上游的错；`exec.Command` 在 `internal/wechat/key` 与
 * `cmd_key.go` 里现读 0 处 ⇒ 这一发没有第二层进程要杀，杀掉直接子进程就是杀掉全部。
 */
export function describeKeyFailure(input: {
  stderr?: unknown
  status?: number | null
  signal?: string | null
  code?: string | null
  timedOut?: boolean
}): string {
  const raw = Buffer.isBuffer(input.stderr)
    ? input.stderr.toString('utf8')
    : typeof input.stderr === 'string'
      ? input.stderr
      : ''
  const lines = raw
    .split(/\r?\n/)
    .map((line) => line.replace(HEX_RUN, (hex) => `<hex×${hex.length}>`).trim())
    .filter((line) => line.length > 0 && ERROR_LINE.test(line))
    .slice(-3)
  // 连进程都没起来（ENOENT = 产物不在、EACCES = 不可执行）：这跟"跑完了但没拿到 key"是不同的两件事，
  // 不许混成一句。`error.message` 里带命令行，所以只取 `code` 那一个词。
  const how = input.timedOut
    ? `超时（${KEY_SCAN_TIMEOUT_MS / 1000} 秒，进程已被杀；上游 key 那条路不生子进程）`
    : input.status == null && input.code
      ? `没能起来（系统错码 ${input.code}，多半是产物路径不可执行）`
      : `退出码 ${input.status ?? '未知'}${input.signal ? ` / 信号 ${input.signal}` : ''}`

  return lines.length > 0
    ? `chatlog key ${how}：${lines.join(' | ').slice(0, 240)}`
    : `chatlog key ${how}：上游 stderr 里没有错误行（stdio 全丢 stdout，密钥不经此处）`
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

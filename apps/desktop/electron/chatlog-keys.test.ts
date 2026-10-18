/**
 * chatlog 密钥交接的钉（node:test，不是 vitest）：
 * `npm run test:desktop:platforms`，或 `npx tsx --test electron/chatlog-keys.test.ts`。
 *
 * 这一档测的是**边界**，不是功能：密钥值不许出现在命令行、台账、返回值里；
 * sidecar 的配置目录不许落 C 盘。上游那两个会把密钥打进 stdout 的位置
 * （`cmd_key.go:34` 的 `fmt.Println(ret)`、`conf.go:93` 的 `server config: %s`）是这些断言存在的原因。
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  chatlogConfigDir,
  describeKeyFailure,
  handoffServerConfig,
  KEY_SCAN_TIMEOUT_MS,
  keyFingerprint,
  PROCESS_CONFIG_FILE,
  SERVER_CONFIG_FILE,
  SIDECAR_ADDR,
  serverConfigReady
} from './chatlog-keys'

const FAKE_KEY = 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'
const FAKE_IMG_KEY = '00112233445566778899aabbccddeeff'

function tempConfigDir(t: { after: (fn: () => void) => void }): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-chatlog-keys-'))

  t.after(() => fs.rmSync(dir, { force: true, recursive: true }))

  return dir
}

function writeProcessConfig(dir: string, extra: Record<string, unknown> = {}): void {
  fs.writeFileSync(
    path.join(dir, PROCESS_CONFIG_FILE),
    JSON.stringify(
      {
        type: 'wechat',
        account: 'wxid_fakeaccount',
        platform: 'windows',
        version: 4,
        full_version: '4.0.0',
        data_dir: 'D:/Data/wechat/xwechat_files/wxid_fakeaccount/db_storage',
        data_key: FAKE_KEY,
        img_key: FAKE_IMG_KEY,
        files: [{ path: 'session.db', size: 1 }],
        ...extra
      },
      null,
      2
    ),
    'utf8'
  )
}

test('配置目录钉在 Plobi 家目录下，不是 C 盘那个 ~/.chatlog', () => {
  const dir = chatlogConfigDir(path.join('D:', 'Data', 'AppData', 'plobi'))

  assert.equal(dir, path.join('D:', 'Data', 'AppData', 'plobi', 'chatlog'))
  assert.ok(!dir.includes('.chatlog'), '别再让 sidecar 往 C:\\Users\\<user>\\.chatlog 写')
})

test('指纹不是密钥本身，且同键同指纹、换键换指纹', () => {
  const first = keyFingerprint(FAKE_KEY)

  assert.equal(first.length, 8)
  assert.notEqual(first, FAKE_KEY)
  assert.ok(!FAKE_KEY.includes(first))
  assert.equal(first, keyFingerprint(FAKE_KEY))
  assert.notEqual(first, keyFingerprint(FAKE_IMG_KEY))
})

test('没跑过 key：报"没有配置文件"，不冒充 ready，也不写出半个服务端配置', (t) => {
  const dir = tempConfigDir(t)
  const result = handoffServerConfig(dir, path.join(dir, 'work'))

  assert.equal(result.ok, false)
  assert.equal(result.reason, 'no-process-config')
  assert.equal(fs.existsSync(path.join(dir, SERVER_CONFIG_FILE)), false)
})

test('配置里没 data_key：单独一格，不与"没产物"混', (t) => {
  const dir = tempConfigDir(t)

  writeProcessConfig(dir, { data_key: '', img_key: '' })

  const result = handoffServerConfig(dir, path.join(dir, 'work'))

  assert.equal(result.ok, false)
  assert.equal(result.reason, 'no-key')
})

test('交接出来的服务端配置：地址/工作目录/auto_decrypt 齐，且不含 account 与 files', (t) => {
  const dir = tempConfigDir(t)

  writeProcessConfig(dir)

  const result = handoffServerConfig(dir, path.join(dir, 'work'))

  assert.equal(result.ok, true)
  const written = JSON.parse(fs.readFileSync(path.join(dir, SERVER_CONFIG_FILE), 'utf8'))

  assert.equal(written.http_addr, SIDECAR_ADDR)
  assert.equal(written.auto_decrypt, true)
  assert.equal(written.work_dir, path.join(dir, 'work'))
  assert.equal(written.platform, 'windows')
  assert.equal(written.version, 4)
  assert.equal(written.data_key, FAKE_KEY, '密钥留在 sidecar 自己的配置文件里（同一把真源），不抄进命令行')
  assert.deepEqual(Object.keys(written).sort(), ['auto_decrypt', 'data_dir', 'data_key', 'http_addr', 'img_key', 'platform', 'type', 'version', 'work_dir'])
  assert.equal('account' in written, false, 'account 不是服务端要的属性，别顺手整包拷贝')
  assert.equal('files' in written, false)
})

test('返回值只给指纹，不给密钥', (t) => {
  const dir = tempConfigDir(t)

  writeProcessConfig(dir)

  const result = handoffServerConfig(dir, path.join(dir, 'work'))
  const serialized = JSON.stringify(result)

  assert.equal(result.ok, true)
  assert.equal(result.fingerprint, keyFingerprint(FAKE_KEY))
  assert.equal(serialized.includes(FAKE_KEY), false, '指纹之外的密钥值不许从这一档流出去')
})

test('幂等：已经交接过就不再摸一次微信，也不重写文件', (t) => {
  const dir = tempConfigDir(t)

  writeProcessConfig(dir)
  handoffServerConfig(dir, path.join(dir, 'work'))

  const before = fs.statSync(path.join(dir, SERVER_CONFIG_FILE)).mtimeMs

  // 把进程配置删掉：如果实现去重取密钥或重写，这里就会变红——它应当只认服务端那份已就绪的配置。
  fs.rmSync(path.join(dir, PROCESS_CONFIG_FILE))

  const again = handoffServerConfig(dir, path.join(dir, 'work'))

  assert.equal(again.ok, true)
  assert.equal(again.wroteServerConfig, false, '第二次不该再写一遍')
  assert.equal(serverConfigReady(dir), true)
  assert.equal(fs.statSync(path.join(dir, SERVER_CONFIG_FILE)).mtimeMs, before)
})

test('main.ts 里不许再把密钥拼进命令行，也不许把 sidecar 的 stdout 逐行记进台账', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, 'main.ts'), 'utf8')

  assert.equal(source.includes("'--data-key'"), false, '密钥上命令行 = 任何按 CommandLine 采样的会话都读得到')
  assert.equal(source.includes("'--img-key'"), false)
  assert.match(source, /prepareChatlogSidecar\(exe, keys\.dataKey\)/, '取密钥这一步必须在 spawn 之前，否则 server 直接退出')
  assert.equal(source.includes("rememberLog(`[chatlog] ${line}`)"), false, '上游那句 server config 里含 data_key')
  assert.match(source, /stdio: \['ignore', 'ignore', 'ignore'\]/)
  assert.match(source, /CHATLOG_DIR: chatlogConfigDir\(PLOBI_HOME\)/, '配置目录必须钉在 D 盘家目录')
})

test('describeKeyFailure 交的是上游那句错，不替它猜原因', () => {
  const detail = describeKeyFailure({
    stderr: Buffer.from('2026-10-18T09:30:00+08:00 INF starting\n2026-10-18T09:32:47+08:00 ERR key scan error="no valid key found"\n'),
    status: 1,
    signal: null
  })

  assert.match(detail, /no valid key found/, '判据③：失败那句要换成上游自己的错')
  assert.match(detail, /退出码 1/)
  assert.equal(detail.includes('PC 微信在登录态吗'), false, '这句把 10-11 实测排除过的三条原因当唯一解释')
})

test('上游 stderr 里的十六进制段一律遮掉，密钥形状不许进台账', () => {
  const detail = describeKeyFailure({
    stderr: Buffer.from(`ERR decrypt failed key=${FAKE_KEY} xor=0123456789abcdef0123456789`),
    status: 1
  })

  assert.equal(/[0-9a-fA-F]{12,}/.test(detail), false, detail)
  assert.match(detail, /<hex×64>/)
})

test('错误行只取尾部三条，整句有上限', () => {
  const many = Array.from({ length: 40 }, (_unused, i) => `ERR line ${i} failed`).join('\n')
  const detail = describeKeyFailure({ stderr: Buffer.from(many), status: 1 })

  assert.equal(detail.split(' | ').length, 3, detail)
  assert.match(detail, /line 39 failed/, '要的是最后三条，不是最前面三条')
  assert.ok(detail.length <= 300, `整句该有上限，实际 ${detail.length}`)
})

test('超时那一档只说"被我们杀了"，不假装拿到了上游的错', () => {
  const detail = describeKeyFailure({ stderr: undefined, status: null, signal: 'SIGKILL', timedOut: true })

  assert.match(detail, /超时/)
  assert.equal(/[0-9a-fA-F]{12,}/.test(detail), false)
})

test('连进程都没起来是另一种失败，不许混成"跑完了但没拿到 key"', () => {
  const detail = describeKeyFailure({ stderr: undefined, status: null, code: 'ENOENT' })

  assert.match(detail, /没能起来（系统错码 ENOENT/)
  assert.equal(detail.includes('退出码'), false, '没跑起来的进程没有退出码')
})

test('超时那把尺必须大过实测一趟失败全扫的 167 秒（裁定 124 一）', () => {
  assert.ok(KEY_SCAN_TIMEOUT_MS >= 170_000, `KEY_SCAN_TIMEOUT_MS=${KEY_SCAN_TIMEOUT_MS} 会先杀掉扫描，面上只剩 ETIMEDOUT`)
})

test('main.ts 那一小段：超时按实测给、只多接一路 stderr、stdout 仍然一路不接', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, 'main.ts'), 'utf8')
  // 断言只看那一发 `chatlog key` 自己的选项块：main.ts 别处有合法的 ['ignore','pipe','pipe']，
  // 拿全文件做子串判断会把它一起禁掉——那是挡别人的刀，不是钉这一格。
  const keyCall = source.match(/execFileSync\(\s*exe,\s*\['key'\],\s*\{[\s\S]{0,700}?\}\s*\)/)
  assert.ok(keyCall, '找不到取密钥那一发（改了写法就同步改这条守卫）')

  assert.equal(source.includes('timeout: 90000'), false, '90 秒 < 实测 167 秒：必先杀进程再拿不到上游那句错')
  assert.match(keyCall[0], /timeout: KEY_SCAN_TIMEOUT_MS/)
  assert.match(keyCall[0], /stdio: \['ignore', 'ignore', 'pipe'\]/, '只多接 stderr 这一路')
  assert.equal(keyCall[0].includes("'pipe', 'pipe'"), false, '接 stdout = 接 cmd_key.go:34 打出来的密钥本体')
  assert.match(source, /detail: describeKeyFailure\(/, '失败那句由 describeKeyFailure 生成，别再手写原因')
  // 禁的是"这句话被当成结论交出去"，不是禁注释提它——注释得说清为什么改。按行判：
  // 出现这句话的那一行必须是注释行（`//`、` * `、`/*`），不能是模板串或字符串。
  const quoted = source
    .split(/\r?\n/)
    .map((line, i) => ({ line, n: i + 1 }))
    .filter(({ line }) => line.includes('PC 微信在登录态吗'))
    .filter(({ line }) => !/^\s*(\/\/|\*|\/\*)/.test(line))
  assert.deepEqual(quoted, [], '这句把 10-11 已实测排除的三条原因当唯一解释（裁定 124）')
})

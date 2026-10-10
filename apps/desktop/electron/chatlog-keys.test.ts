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
  handoffServerConfig,
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

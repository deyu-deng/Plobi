/**
 * WP-DATA-ISOLATION 的判据测试。
 *
 * 这套断言盯的是一件事：**装机版不能被开发机的门牌带偏**。过去它读
 * `HKCU\Environment` 里的 `PLOBI_HOME`，于是开发态一改，玩家那份装机版
 * 立刻读到同一格数据。下面第 2 条用例就是那件事的警报，去掉它就等于
 * 把这层保护又拆了。
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  directoryUsable,
  resolveDataRoot,
  resolveElectronUserDataRoot,
  dataRootLayout
} from './data-root'

const LOCAL = path.resolve(path.sep === '\\' ? 'D:\\Data\\AppData' : '/data/appdata')
const HOME = path.resolve(path.sep === '\\' ? 'C:\\Users\\tester' : '/home/tester')

test('装机版：安装时选的那一格优先于默认格', () => {
  const chosen = path.resolve(path.sep === '\\' ? 'E:\\PlobiData' : '/mnt/plobi-data')
  const got = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: chosen,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.root, chosen)
  assert.equal(got.source, 'install-record')
})

test('装机版：忽略继承来的 PLOBI_HOME —— 隔离的底线', () => {
  const got = resolveDataRoot({
    isPackaged: true,
    // 开发机 `setx PLOBI_HOME D:\Data\AppData\plobi` 之后，装机版会原样继承到它。
    homeEnv: path.resolve(path.sep === '\\' ? 'D:\\Data\\AppData\\plobi' : '/data/appdata/plobi'),
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.source, 'default-packaged')
  assert.equal(got.root, path.join(LOCAL, 'Plobi'))
  assert.notEqual(got.root, path.join(LOCAL, 'plobi'))
})

test('装机版：没选过数据目录就落到自己的默认格 %LOCALAPPDATA%\\Plobi', () => {
  const got = resolveDataRoot({ isPackaged: true, localAppData: LOCAL, home: HOME })
  assert.equal(got.root, path.join(LOCAL, 'Plobi'))
  assert.equal(got.source, 'default-packaged')
})

test('开发态：PLOBI_HOME 说了算', () => {
  const devHome = path.resolve(path.sep === '\\' ? 'D:\\Data\\AppData\\Plobi-Dev' : '/data/appdata/Plobi-Dev')
  const got = resolveDataRoot({ isPackaged: false, homeEnv: devHome, localAppData: LOCAL, home: HOME })
  assert.equal(got.root, devHome)
  assert.equal(got.source, 'home-env')
})

test('开发态：没设 PLOBI_HOME 就落到 Plobi-Dev，不再碰老的 plobi', () => {
  const got = resolveDataRoot({ isPackaged: false, localAppData: LOCAL, home: HOME })
  assert.equal(got.root, path.join(LOCAL, 'Plobi-Dev'))
  assert.equal(got.source, 'default-dev')
})

test('显式注入的目录两边都最优先，且压过安装记录与 PLOBI_HOME', () => {
  const pinned = path.resolve(path.sep === '\\' ? `D:\\Temp\\${'plobi-e2e'}` : '/tmp/plobi-e2e')
  for (const isPackaged of [true, false]) {
    const got = resolveDataRoot({
      isPackaged,
      explicitDir: pinned,
      homeEnv: path.resolve(LOCAL, 'plobi'),
      installRecordedDir: path.resolve(LOCAL, 'recorded'),
      localAppData: LOCAL,
      home: HOME
    })
    assert.equal(got.root, pinned)
    assert.equal(got.source, 'explicit-env')
  }
})

test('尾斜杠与空白不该改变判定结果', () => {
  const base = path.resolve(path.sep === '\\' ? 'E:\\PlobiData' : '/mnt/plobi-data')
  const got = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: `  ${base}${path.sep}  `,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.root, base)
})

test('空白的安装记录当作没选，落回默认格而不是 cwd', () => {
  const got = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: '   ',
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.root, path.join(LOCAL, 'Plobi'))
  assert.equal(got.source, 'default-packaged')
})

test('Electron userData：开发态与装机版不共用一把单实例锁', () => {
  assert.equal(
    resolveElectronUserDataRoot({ isPackaged: true, appDataDir: LOCAL, appName: 'plobi' }),
    path.join(LOCAL, 'plobi')
  )
  assert.equal(
    resolveElectronUserDataRoot({ isPackaged: false, appDataDir: LOCAL, appName: 'plobi' }),
    path.join(LOCAL, 'plobi-dev')
  )
})

test('数据落地：agent 根目录跟着数据目录走', () => {
  const root = path.join(LOCAL, 'Plobi')
  const layout = dataRootLayout(root)
  assert.equal(layout.agentRoot, path.join(root, 'plobi-agent'))
  assert.equal(layout.connectionConfig, path.join(root, 'connection.json'))
})

// ── 开发态自愈（2026-11-08）────────────────────────────────────────────────
// 装机版安装器至今还会往 HKCU\Environment 写 PLOBI_HOME 指向安装目录。开发态
// 不能再指望"它以后别写了"：一旦被写成装机目录，必须自己认出来并站回 Plobi-Dev。

const RECORDED = path.resolve(path.sep === '\\' ? 'D:\\Data\\AppData\\Plobi' : '/data/appdata/Plobi')
const DEV_HOME = path.resolve(path.sep === '\\' ? 'D:\\Data\\AppData\\Plobi-Dev' : '/data/appdata/Plobi-Dev')

test('开发态：PLOBI_HOME 被安装器污染成装机目录时，回落 Plobi-Dev', () => {
  const got = resolveDataRoot({
    isPackaged: false,
    homeEnv: RECORDED, // 安装器写进去的那一格
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.source, 'default-dev-polluted')
  assert.equal(got.root, path.join(LOCAL, 'Plobi-Dev'))
  assert.notEqual(got.root, RECORDED)
})

test('开发态：污染识别不看写法（尾斜杠/大小写不同也算同一格）', () => {
  const got = resolveDataRoot({
    isPackaged: false,
    homeEnv: `${RECORDED.toUpperCase()}${path.sep}`,
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.source, 'default-dev-polluted')
  assert.equal(got.root, path.join(LOCAL, 'Plobi-Dev'))
})

test('开发态：PLOBI_DEV_HOME 优先于（哪怕被污染的）PLOBI_HOME', () => {
  const got = resolveDataRoot({
    isPackaged: false,
    devHomeEnv: DEV_HOME,
    homeEnv: RECORDED,
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.source, 'dev-home-env')
  assert.equal(got.root, DEV_HOME)
})

test('开发态：PLOBI_DEV_HOME 自己指到装机目录也作废', () => {
  const got = resolveDataRoot({
    isPackaged: false,
    devHomeEnv: RECORDED,
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(got.source, 'default-dev-polluted')
  assert.equal(got.root, path.join(LOCAL, 'Plobi-Dev'))
})

test('装机版：永不读 PLOBI_DEV_HOME —— 那是开发态的钥匙', () => {
  const withRecord = resolveDataRoot({
    isPackaged: true,
    devHomeEnv: DEV_HOME,
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(withRecord.source, 'install-record')
  assert.equal(withRecord.root, RECORDED)

  const withoutRecord = resolveDataRoot({
    isPackaged: true,
    devHomeEnv: DEV_HOME,
    localAppData: LOCAL,
    home: HOME
  })
  assert.equal(withoutRecord.source, 'default-packaged')
  assert.equal(withoutRecord.root, path.join(LOCAL, 'Plobi'))
})

// ── 数据目录摸不到时的回退（2026-11-10）────────────────────────────────────
// 安装器允许把数据目录手打到副盘/移动硬盘（`E:\PlobiData`）。那块盘不在这台
// 机器上时，旧行为会拿一格不存在的地方去首启 mkdir ⇒ ENOENT 把启动掐死。
// 现在：候选摸不到就让位给下一档，source 说清楚"谁让的位"。

const UNREACHABLE = path.resolve(path.sep === '\\' ? 'E:\\PlobiData' : '/mnt/absent/PlobiData')
const only = (allowed: string[]) => (dir: string) => allowed.some((a) => path.resolve(a) === path.resolve(dir))

test('装机版：选的数据目录那块盘不在了 ⇒ 退默认格，source 指出原因', () => {
  const got = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: UNREACHABLE,
    localAppData: LOCAL,
    home: HOME,
    isDirUsable: only([path.join(LOCAL, 'Plobi')])
  })
  assert.equal(got.root, path.join(LOCAL, 'Plobi'))
  assert.equal(got.source, 'install-record-unusable')
})

test('装机版：录的那一格与默认格都摸不到 ⇒ 仍返回默认格但标记为不可用', () => {
  const got = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: UNREACHABLE,
    localAppData: LOCAL,
    home: HOME,
    isDirUsable: () => false
  })
  assert.equal(got.root, path.join(LOCAL, 'Plobi'))
  assert.equal(got.source, 'default-packaged-unusable')
})

test('显式注入的目录摸不到时**不换道** —— 那是有人拿参数指定的', () => {
  const pinned = path.resolve(path.sep === '\\' ? 'Z:\\ci-home' : '/absent/ci-home')
  const got = resolveDataRoot({
    isPackaged: true,
    explicitDir: pinned,
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME,
    isDirUsable: () => false
  })
  assert.equal(got.root, pinned)
  assert.equal(got.source, 'explicit-env-unusable')
})

test('开发态：PLOBI_HOME 指到不存在的盘 ⇒ 回落 Plobi-Dev', () => {
  const got = resolveDataRoot({
    isPackaged: false,
    homeEnv: UNREACHABLE,
    localAppData: LOCAL,
    home: HOME,
    isDirUsable: only([path.join(LOCAL, 'Plobi-Dev')])
  })
  assert.equal(got.root, path.join(LOCAL, 'Plobi-Dev'))
  assert.equal(got.source, 'home-env-unusable')
})

test('开发态：PLOBI_DEV_HOME 摸不到但 PLOBI_HOME 摸得到 ⇒ 用后者并标记原因', () => {
  const got = resolveDataRoot({
    isPackaged: false,
    devHomeEnv: UNREACHABLE,
    homeEnv: DEV_HOME,
    localAppData: LOCAL,
    home: HOME,
    isDirUsable: only([DEV_HOME])
  })
  assert.equal(got.root, DEV_HOME)
  assert.equal(got.source, 'dev-home-env-unusable')
})

test('探针存在时，能用的那一档仍按原优先级胜出（不因探测改变老行为）', () => {
  const got = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: RECORDED,
    localAppData: LOCAL,
    home: HOME,
    isDirUsable: only([RECORDED])
  })
  assert.equal(got.root, RECORDED)
  assert.equal(got.source, 'install-record')
})

test('directoryUsable：真目录算能用，不存在的子目录建在真目录下也算能用', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'plobi-data-root-'))
  try {
    assert.equal(directoryUsable(base), true)
    assert.equal(directoryUsable(path.join(base, 'plobi-agent', 'venv')), true)
    // 同名文件占位 ⇒ 不是目录 ⇒ 摸不到（往里 mkdir 会 EEXIST/ENOTDIR）。
    const blocker = path.join(base, 'blocked')
    fs.writeFileSync(blocker, '')
    assert.equal(directoryUsable(blocker), false)
    assert.equal(directoryUsable(path.join(blocker, 'child')), false)
  } finally {
    fs.rmSync(base, { recursive: true, force: true })
  }
})


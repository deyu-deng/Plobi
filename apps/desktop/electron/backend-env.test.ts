import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import {
  appendUniquePathEntries,
  buildDesktopBackendEnv,
  buildDesktopBackendPath,
  dedupeEnvKeys,
  desktopCliSearchPath,
  normalizePlobiHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES,
  resolveBrainSurfacePointers
} from './backend-env'
import { resolveDataRoot } from './data-root'

// 2026-11-07：从**开始菜单**启动的那个 app，它的进程环境是登录那一刻的快照；
// 之后写进 HKCU 的 MIND_ROOT／SURFACE_ROOT 它看不见 ⇒ 后端就找不到脑。
// 这一格按 env → 注册表 解析，并把活的那一份交给后端。
test('脑与人读面：env 有就用 env，没有就读注册表那份', () => {
  const fromEnv = resolveBrainSurfacePointers({
    currentEnv: { MIND_ROOT: 'D:\\Data\\AppData\\Plobi-Dev\\mind' },
    platform: 'win32',
    readEnvVar: () => 'D:\\Data\\AppData\\Plobi-Dev\\mind'
  })

  assert.equal(fromEnv.MIND_ROOT, 'D:\\Data\\AppData\\Plobi-Dev\\mind')

  const fromRegistry = resolveBrainSurfacePointers({
    currentEnv: {},
    platform: 'win32',
    readEnvVar: (name: string) => (name === 'MIND_ROOT' ? 'D:\\Data\\AppData\\Plobi-Dev\\mind' : 'D:\\Data\\AppData\\Plobi-Dev')
  })

  assert.equal(fromRegistry.MIND_ROOT, 'D:\\Data\\AppData\\Plobi-Dev\\mind')
  assert.equal(fromRegistry.SURFACE_ROOT, 'D:\\Data\\AppData\\Plobi-Dev')
})

test('脑与人读面：继承来的那串已经不在了，注册表那份还在 ⇒ 改读注册表那份', () => {
  const pointers = resolveBrainSurfacePointers({
    currentEnv: { MIND_ROOT: 'C:\\Users\\Turtlelet\\AppData\\Local\\Plobi\\mind' },
    platform: 'win32',
    readEnvVar: () => 'D:\\Data\\AppData\\Plobi-Dev\\mind',
    exists: (candidate: string) => candidate.startsWith('D:\\')
  })

  assert.equal(pointers.MIND_ROOT, 'D:\\Data\\AppData\\Plobi-Dev\\mind')
})

test('脑与人读面：两格都读不到 ⇒ 什么都不给（让 Python 侧按它自己的默认走）', () => {
  const pointers = resolveBrainSurfacePointers({
    currentEnv: {},
    platform: 'linux',
    readEnvVar: () => null
  })

  assert.deepEqual(pointers, {})
})

test('显式传入的 brainSurface 仍然优先（老调用点兼容口）', () => {
  const env = buildDesktopBackendEnv({
    plobiHome: 'D:\\Data\\AppData\\Plobi',
    venvRoot: 'D:\\Data\\AppData\\Plobi\\plobi-agent\\venv',
    platform: 'win32',
    pathModule: path.win32,
    currentEnv: {},
    brainSurface: { MIND_ROOT: 'D:\\Data\\AppData\\Plobi-Dev\\mind', SURFACE_ROOT: 'D:\\Data\\AppData\\Plobi-Dev' }
  })

  assert.equal(env.MIND_ROOT, 'D:\\Data\\AppData\\Plobi-Dev\\mind')
  assert.equal(env.SURFACE_ROOT, 'D:\\Data\\AppData\\Plobi-Dev')
})

test('desktop backend PATH adds Plobi-managed bins and missing POSIX sane entries', () => {
  const result = buildDesktopBackendPath({
    plobiHome: '/Users/test/.plobi',
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentPath: '/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin',
    platform: 'darwin',
    pathModule: path.posix
  })

  const entries = result.split(':')
  assert.equal(entries[0], '/Users/test/.plobi/node/bin')
  assert.equal(entries[1], '/Users/test/.plobi/plobi-agent/venv/bin')
  assert.ok(entries.includes('/opt/homebrew/bin'), 'Apple Silicon Homebrew bin is added')
  assert.ok(entries.includes('/opt/homebrew/sbin'), 'Apple Silicon Homebrew sbin is added')
  assert.ok(entries.includes('/usr/local/sbin'), 'missing standard sbin is added')

  for (const expected of POSIX_SANE_PATH_ENTRIES) {
    assert.ok(entries.includes(expected), `${expected} should be present`)
  }
})

test('desktop backend PATH preserves first occurrence and avoids duplicates', () => {
  const result = buildDesktopBackendPath({
    plobiHome: '/Users/test/.plobi',
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentPath: '/opt/homebrew/bin:/usr/bin:/opt/homebrew/bin:/bin',
    platform: 'darwin',
    pathModule: path.posix
  })

  const entries = result.split(':')
  assert.equal(entries.filter(entry => entry === '/opt/homebrew/bin').length, 1)
  assert.ok(
    entries.indexOf('/opt/homebrew/bin') < entries.indexOf('/opt/homebrew/sbin'),
    'existing Homebrew bin keeps its precedence over appended missing sane entries'
  )
})

test('buildDesktopBackendEnv extends PYTHONPATH and backend PATH together', () => {
  const env = buildDesktopBackendEnv({
    plobiHome: '/Users/test/.plobi',
    pythonPathEntries: ['/repo/plobi-agent'],
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentEnv: {
      PATH: '/usr/bin:/bin',
      PYTHONPATH: '/existing/pythonpath'
    },
    platform: 'darwin',
    pathModule: path.posix
  })

  assert.equal(env.PYTHONPATH, '/repo/plobi-agent:/existing/pythonpath')
  assert.ok(env.PATH.startsWith('/Users/test/.plobi/node/bin:/Users/test/.plobi/plobi-agent/venv/bin:'))
  assert.ok(env.PATH.includes('/opt/homebrew/bin'))
})

test('normalizePlobiHomeRoot maps profile homes back to the global Plobi root', () => {
  assert.equal(
    normalizePlobiHomeRoot('/Users/test/.plobi/profiles/oracle', { pathModule: path.posix }),
    '/Users/test/.plobi'
  )
  assert.equal(
    normalizePlobiHomeRoot('C:\\Users\\test\\AppData\\Local\\plobi\\profiles\\oracle', { pathModule: path.win32 }),
    'C:\\Users\\test\\AppData\\Local\\plobi'
  )
  assert.equal(normalizePlobiHomeRoot('/Users/test/.plobi', { pathModule: path.posix }), '/Users/test/.plobi')
})

test('Windows PATH casing and delimiter are preserved without POSIX sane entries', () => {
  const env = buildDesktopBackendEnv({
    plobiHome: 'C:\\Users\\test\\AppData\\Local\\plobi',
    pythonPathEntries: ['C:\\repo\\plobi-agent'],
    venvRoot: 'C:\\Users\\test\\AppData\\Local\\plobi\\plobi-agent\\venv',
    currentEnv: {
      Path: 'C:\\Windows\\System32;C:\\Windows',
      PYTHONPATH: 'C:\\existing\\pythonpath'
    },
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(pathEnvKey({ Path: 'x' }, 'win32'), 'Path')
  assert.equal(env.PATH, undefined)
  assert.ok(env.Path.startsWith('C:\\Users\\test\\AppData\\Local\\plobi\\node\\bin;'))
  assert.ok(env.Path.includes('\\venv\\Scripts;'))
  assert.ok(env.Path.includes(';C:\\Windows\\System32;C:\\Windows'))
  assert.equal(env.Path.includes('/opt/homebrew/bin'), false)
})

test('appendUniquePathEntries drops empty entries and keeps first occurrence', () => {
  assert.equal(appendUniquePathEntries([':/a::/b', ['/a', '/c']], { delimiter: ':' }), '/a:/b:/c')
})

test('desktopCliSearchPath finds a plobi under ~/.local/bin when the GUI PATH omits it', () => {
  // The exact Finder/Dock case: launchd hands the app /usr/bin:/bin:/usr/sbin:/sbin,
  // which never includes ~/.local/bin — where the user's working `plobi` symlink
  // lives. The injected per-user dirs make an already-installed CLI reachable
  // without a shell. Nothing here touches the real filesystem or PATH.
  const guiPath = '/usr/bin:/bin:/usr/sbin:/sbin'
  const search = desktopCliSearchPath({
    home: '/Users/test',
    plobiHome: '/Users/test/.plobi',
    venvRoot: '/Users/test/.plobi/plobi-agent/venv',
    currentPath: guiPath,
    platform: 'darwin',
    pathModule: path.posix
  })

  const dirs = search.split(':')
  assert.ok(dirs.includes('/Users/test/.local/bin'), 'per-user ~/.local/bin is added')
  assert.ok(dirs.includes('/Users/test/.cargo/bin'), 'per-user ~/.cargo/bin is added')
  assert.ok(dirs.includes('/opt/homebrew/bin'), 'the backend sane-PATH dirs are reused')

  // findOnPath-equivalent probe over that PATH, with a plobi present only in the
  // injected ~/.local/bin, must locate it.
  const present = new Set(['/Users/test/.local/bin/plobi'])
  const found = dirs.map(dir => path.posix.join(dir, 'plobi')).find(candidate => present.has(candidate))
  assert.equal(found, '/Users/test/.local/bin/plobi', 'a CLI in ~/.local/bin is discovered')
})

test('desktopCliSearchPath leaves the Windows search path unchanged', () => {
  // Windows CLI discovery is installer/registry-driven; a bare dir scan there can
  // resolve the wrong extensionless file, so no per-user dirs are prepended.
  const search = desktopCliSearchPath({
    home: 'C:\\Users\\test',
    plobiHome: 'C:\\Users\\test\\.plobi',
    venvRoot: 'C:\\Users\\test\\.plobi\\plobi-agent\\venv',
    currentPath: 'C:\\Windows\\System32',
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(search.includes('.local'), false)
  assert.equal(search.includes('.cargo'), false)
})

// ─── 大小写重复键：沙箱代理塞的 HTTPS_PROXY + https_proxy ──────────────────
//
// 2026-10-22 现核的一整串故障的根因（桌面连不上后端、L2 进不去）：
// WorkBuddy 沙箱起子进程时同时塞了大写与小写两套 proxy 变量（值相同）。
// Node 的 env 大小写敏感、两个键都活着；可一旦交给 PowerShell / .NET，
// 环境字典大小写不敏感，于是 "An item with the same key has already been added"
// ⇒ Start-Process 抛 ArgumentException ⇒ 子进程 exit=1073807364
// ⇒ bootstrap 的 config-templates 阶段 "produced no JSON result frame"
// ⇒ 后端起不来 ⇒ 桌面 "Timed out connecting to Plobi backend after 15000ms"。
//
// 同一台机器上 `Get-ChildItem Env:` 都会炸，所以这不是我们写错，是继承来的
// 环境本身不合法——必须在自己这一侧把它修合法。

test('dedupeEnvKeys keeps one spelling per key (last write wins)', () => {
  const result = dedupeEnvKeys({
    HTTPS_PROXY: 'http://127.0.0.1:56359',
    https_proxy: 'http://127.0.0.1:56359',
    HTTP_PROXY: 'http://127.0.0.1:1',
    http_proxy: 'http://127.0.0.1:2',
    PATH: '/usr/bin'
  })

  const keys = Object.keys(result)
  assert.equal(keys.filter(key => key.toLowerCase() === 'https_proxy').length, 1)
  assert.equal(keys.filter(key => key.toLowerCase() === 'http_proxy').length, 1)
  // 后写覆盖先写（与 Windows 环境块同语义）
  assert.equal(result.https_proxy, 'http://127.0.0.1:56359')
  assert.equal(result.http_proxy, 'http://127.0.0.1:2')
  assert.equal(result.PATH, '/usr/bin')
})

test('dedupeEnvKeys leaves a clean env untouched', () => {
  const clean = { PATH: '/usr/bin', HOME: '/home/test', PLOBI_HOME: '/home/test/.plobi' }
  assert.deepEqual(dedupeEnvKeys(clean), clean)
})

test('buildDesktopBackendEnv still adds PYTHONPATH and PATH when the inherited env is dirty', () => {
  const result = buildDesktopBackendEnv({
    plobiHome: '/home/test/.plobi',
    venvRoot: '/home/test/.plobi/plobi-agent/venv',
    pythonPathEntries: ['/home/test/.plobi/plobi-agent'],
    currentEnv: {
      HTTPS_PROXY: 'http://127.0.0.1:56359',
      https_proxy: 'http://127.0.0.1:56359',
      PATH: '/usr/bin'
    },
    platform: 'linux',
    pathModule: path.posix
  })

  assert.ok(result.PYTHONPATH.includes('/home/test/.plobi/plobi-agent'), 'PYTHONPATH still computed')
  assert.ok(result.PATH.includes('/home/test/.plobi/plobi-agent/venv/bin'), 'PATH still extended')
})

// ─── 双身份隔离（2026-11-08）────────────────────────────────────────────────
//
// 这台机器上装了两份 Plobi：开发态（`npm run dev`，家 = `D:\Data\AppData\Plobi-Dev`）
// 与装机版（家在安装时选、记在 `HKCU\Software\Plobi\DataDir`，现为
// `D:\Data\AppData\Plobi`）。过去安装器把 `PLOBI_HOME`／`MIND_ROOT`／`SURFACE_ROOT`
// 三格写进 `HKCU\Environment`，于是两份身份共用同一块门牌：谁后装，谁就把
// 对方带偏。
//
// 现在的契约：**数据根是唯一上游**，三格一律由它派生，继承来的 env 与注册表
// 都不再是来源。下面两条把两种身份各钉一遍，任何一条失守都等于隔离被拆。

const WIN_PACKAGED_HOME = 'D:\\Data\\AppData\\Plobi'
const WIN_DEV_HOME = 'D:\\Data\\AppData\\Plobi-Dev'

function backendEnvFor(plobiHome: string, currentEnv: NodeJS.ProcessEnv = {}) {
  return buildDesktopBackendEnv({
    plobiHome,
    venvRoot: `${plobiHome}\\plobi-agent\\venv`,
    pythonPathEntries: [`${plobiHome}\\plobi-agent`],
    currentEnv,
    platform: 'win32',
    pathModule: path.win32
  })
}

test('双身份：装机版后端三格由 installRecordedDir 派生，不再读继承来的 PLOBI_HOME', () => {
  const dataRoot = resolveDataRoot({
    isPackaged: true,
    installRecordedDir: WIN_PACKAGED_HOME,
    // 脏环境：开发态把 PLOBI_HOME 钉在这台机器上时，装机版会原样继承到它。
    homeEnv: WIN_DEV_HOME,
    localAppData: 'C:\\Users\\tester\\AppData\\Local',
    home: 'C:\\Users\\tester'
  })

  assert.equal(dataRoot.root, WIN_PACKAGED_HOME)
  assert.equal(dataRoot.source, 'install-record')

  const env = backendEnvFor(dataRoot.root)
  assert.equal(env.PLOBI_HOME, WIN_PACKAGED_HOME)
  assert.equal(env.MIND_ROOT, `${WIN_PACKAGED_HOME}\\mind`)
  assert.equal(env.SURFACE_ROOT, WIN_PACKAGED_HOME)
})

test('双身份：开发态后端三格由 PLOBI_HOME 环境值派生，绝不落到装机版目录', () => {
  const dataRoot = resolveDataRoot({
    isPackaged: false,
    // 注册表里还留着装机版那一格，开发态不许读它。
    installRecordedDir: WIN_PACKAGED_HOME,
    homeEnv: WIN_DEV_HOME,
    localAppData: 'C:\\Users\\tester\\AppData\\Local',
    home: 'C:\\Users\\tester'
  })

  assert.equal(dataRoot.root, WIN_DEV_HOME)
  assert.equal(dataRoot.source, 'home-env')

  const env = backendEnvFor(dataRoot.root)
  assert.equal(env.PLOBI_HOME, WIN_DEV_HOME)
  assert.equal(env.MIND_ROOT, `${WIN_DEV_HOME}\\mind`)
  assert.equal(env.SURFACE_ROOT, WIN_DEV_HOME)
})

test('双身份：继承来的脏 MIND_ROOT／SURFACE_ROOT 一律作废（脑与家必须同源）', () => {
  const dirty = {
    PLOBI_HOME: WIN_PACKAGED_HOME,
    MIND_ROOT: `${WIN_PACKAGED_HOME}\\mind`,
    SURFACE_ROOT: `${WIN_PACKAGED_HOME}\\surface`
  }

  // 开发态拿到的是登录快照，里面三格全指着装机版 —— 这正是被污染的样子。
  const env = backendEnvFor(WIN_DEV_HOME, dirty)

  assert.equal(env.PLOBI_HOME, WIN_DEV_HOME)
  assert.equal(env.MIND_ROOT, `${WIN_DEV_HOME}\\mind`)
  assert.equal(env.SURFACE_ROOT, WIN_DEV_HOME)
  for (const value of [env.PLOBI_HOME, env.MIND_ROOT, env.SURFACE_ROOT]) {
    assert.equal(String(value).includes('\\Plobi\\'), false, `泄漏了装机版路径：${value}`)
  }
})

test('双身份：继承来的键换了大小写拼写，也只留我们注入的那一格', () => {
  const env = backendEnvFor(WIN_DEV_HOME, { plobi_home: WIN_PACKAGED_HOME, mind_root: `${WIN_PACKAGED_HOME}\\mind` })

  const spellings = Object.keys(env).filter(key => key.toLowerCase() === 'plobi_home')
  assert.deepEqual(spellings, ['PLOBI_HOME'])
  assert.equal(env.PLOBI_HOME, WIN_DEV_HOME)
  assert.equal(env.MIND_ROOT, `${WIN_DEV_HOME}\\mind`)
})

test('没给数据根时退回 resolveBrainSurfacePointers 的旧口径（不造默认）', () => {
  const env = buildDesktopBackendEnv({
    plobiHome: '',
    pythonPathEntries: [],
    // 两格都由 currentEnv 给足；注册表读取一并钉空，免得开发机上残留的
    // MIND_ROOT（现为 Plobi-Dev 派生）把它顶掉，让断言随本机环境飘。
    currentEnv: { MIND_ROOT: `${WIN_PACKAGED_HOME}\\mind`, SURFACE_ROOT: WIN_PACKAGED_HOME },
    readEnvVar: () => '',
    platform: 'win32',
    pathModule: path.win32
  })

  assert.equal(env.PLOBI_HOME, undefined, '没有数据根就不写 PLOBI_HOME')
  assert.equal(env.MIND_ROOT, `${WIN_PACKAGED_HOME}\\mind`)
  assert.equal(env.SURFACE_ROOT, WIN_PACKAGED_HOME)
})

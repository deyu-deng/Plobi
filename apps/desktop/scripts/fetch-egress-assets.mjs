'use strict'
/**
 * fetch-egress-assets.mjs — 把随包的路由引擎四件套下到 `PLOBI_EGRESS_SRC` 认得的布局里。
 *
 * 为什么必须在 CI 里跑：四件套按裁定 134 不进 git，于是 CI checkout 之后候选链全空，
 * `before-pack` 只能往包里写一张 MISSING.txt —— 0.3.2 就是这么发出去的，开关报
 * `engine binary " " is not on disk`。chatlog 早就是「CI 编译 → artifact → 打包时下载 →
 * 硬闸」的闭环，egress 一直没有，这个脚本补的就是那一段。
 *
 * 哪些能钉死、哪些钉不住（2026-11-17 在打包机上现核，不是抄的）：
 *  · mihomo / wintun：官方按 tag 发布，zip 的 sha256 由 release API / 官网给，钉死；解出来的
 *    exe / dll 也各钉一层，因为 `agent/egress/engine_files.py` 钉的是解包后的文件。
 *  · GeoSite.dat / geoip.metadb：meta-rules-dat 只有一个滚动的 `latest`（每天 04:42Z 重建），
 *    官方不保留历史 tag，钉住某个 sha 只会让第二天的构建红。所以这两件只钉「来源 + 结构性
 *    检查」（HTTP 2xx、长度过闸、不是一张 HTML 错误页），下到什么就记什么——记进随包清单
 *    `egress-manifest.json`，运行期 `engine_files.expected()` 以清单为准。
 *
 * 失败即非零退出：缺一件都不许出包（口径：随包全带 + 缺件不许发布）。
 *
 * 用法：
 *   node apps/desktop/scripts/fetch-egress-assets.mjs [--out .egress-src/ship]
 */
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import zlib from 'node:zlib'

const MIHOMO = {
  version: 'v1.19.32',
  url: 'https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-windows-amd64-v1.19.32.zip',
  //: 官方 release API 给的 asset digest（`sha256:` 前缀之后那一段）。
  zipSha256: '1ac84e795b5b915446e20139677fb6cc013a0778e876e8d4c96f994750b4c4a3',
  zipBytes: 22492402,
  //: zip 里的成员名与解出来的指纹，两边钉的是同一份字节（与 engine_files.BINARY 对齐）。
  member: 'mihomo-windows-amd64.exe',
  exeSha256: 'beb9878924d7bd38c67176b441ab5e668cc378bfe751c8dc8fef3eb5aaf566d5',
  exeBytes: 61219840,
  as: 'mihomo.exe'
}

const WINTUN = {
  version: '0.14.1',
  url: 'https://www.wintun.net/builds/wintun-0.14.1.zip',
  //: 官网公布的 zip SHA2-256（engine_files 的注释里也记着这一条）。
  zipSha256: '07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51',
  member: 'wintun/bin/amd64/wintun.dll',
  dllSha256: 'e5da8447dc2c320edc0fc52fa01885c103de8c118481f683643cacc3220dafce',
  dllBytes: 427552,
  as: 'wintun.dll'
}

const GEO = [
  {
    as: 'GeoSite.dat',
    url: 'https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat',
    //: 只防"下到的是一张错误页 / 半截文件"：正常产物 4 MB 上下。
    minBytes: 1_000_000
  },
  {
    as: 'geoip.metadb',
    url: 'https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb',
    minBytes: 1_000_000
  }
]

/** 落位规则与 `stage-egress-assets.mjs` 的 `EGRESS_ASSETS` 对齐：这两个在 bin/，那两个在根。 */
export const LAYOUT = { 'mihomo.exe': 'bin', 'wintun.dll': 'bin', 'GeoSite.dat': '', 'geoip.metadb': '' }

export function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex')
}

/**
 * 从 zip 里取出一个成员。只用 `node:zlib`，不引第三方解压库：这个脚本要在干净 runner 上跑，
 * 而 `extract-zip` 只是 electron-builder 的传递依赖，不属于我们能保证安装的东西。
 */
export function readZipMember(buffer, member) {
  const EOCD_SIG = 0x06054b50
  const CENTRAL_SIG = 0x02014b50
  const LOCAL_SIG = 0x04034b50
  let eocd = -1

  for (let i = buffer.length - 22; i >= 0 && i > buffer.length - 22 - 0xffff; i -= 1) {
    if (buffer.readUInt32LE(i) === EOCD_SIG) {
      eocd = i
      break
    }
  }

  if (eocd < 0) {
    throw new Error('zip 里找不到 EOCD 记录，这不是一个完整的 zip')
  }

  const count = buffer.readUInt16LE(eocd + 10)
  let offset = buffer.readUInt32LE(eocd + 16)

  for (let i = 0; i < count; i += 1) {
    if (buffer.readUInt32LE(offset) !== CENTRAL_SIG) {
      throw new Error('zip 的中央目录项损坏')
    }

    const method = buffer.readUInt16LE(offset + 10)
    const compressedSize = buffer.readUInt32LE(offset + 20)
    const nameLen = buffer.readUInt16LE(offset + 28)
    const extraLen = buffer.readUInt16LE(offset + 30)
    const commentLen = buffer.readUInt16LE(offset + 32)
    const localOffset = buffer.readUInt32LE(offset + 42)
    const name = buffer.toString('utf8', offset + 46, offset + 46 + nameLen)

    if (name === member) {
      if (buffer.readUInt32LE(localOffset) !== LOCAL_SIG) {
        throw new Error(`zip 里 ${member} 的局部头损坏`)
      }

      const localNameLen = buffer.readUInt16LE(localOffset + 26)
      const localExtraLen = buffer.readUInt16LE(localOffset + 28)
      const start = localOffset + 30 + localNameLen + localExtraLen
      const body = buffer.subarray(start, start + compressedSize)

      if (method === 0) {
        return body
      }

      if (method === 8) {
        return zlib.inflateRawSync(body)
      }

      throw new Error(`zip 里 ${member} 用了不支持的压缩方式 ${method}`)
    }

    offset += 46 + nameLen + extraLen + commentLen
  }

  throw new Error(`zip 里没有 ${member}`)
}

/** 一件东西下完之后的形状检查：长度、指纹、别是 HTML。 */
export function checkBuffer({ name, buffer, bytes = 0, sha = '', minBytes = 0, url = '' }) {
  if (bytes && buffer.length !== bytes) {
    throw new Error(`${name} 长度不符：下到 ${buffer.length} B，钉值 ${bytes} B（${url}）`)
  }

  if (minBytes && buffer.length < minBytes) {
    const head = buffer.subarray(0, 64).toString('utf8').trim().slice(0, 40)

    throw new Error(`${name} 只有 ${buffer.length} B（下限 ${minBytes} B），像是错误页：${head}`)
  }

  const head = buffer.subarray(0, 64).toString('utf8').trim().toLowerCase()

  if (head.startsWith('<!doctype') || head.startsWith('<html')) {
    throw new Error(`${name} 下到的是一张 HTML 页面，不是产物（${url}）`)
  }

  const actual = sha256(buffer)

  if (sha && actual !== sha) {
    throw new Error(`${name} sha256 不符：下到 ${actual}，钉值 ${sha}（${url}）`)
  }

  return actual
}

async function download(url, log) {
  const response = await fetch(url, { redirect: 'follow', headers: { 'user-agent': 'plobi-build' } })

  if (!response.ok) {
    throw new Error(`下载失败：${url} → HTTP ${response.status}`)
  }

  const buffer = Buffer.from(await response.arrayBuffer())

  log(`[egress-fetch] 下到 ${url} (${buffer.length} B)`)

  return buffer
}

/**
 * 下齐四件、逐个核对、按落位规则写到 `outDir`，最后写一张 `egress-manifest.json`。
 * 返回那张清单（测试可以直接断言它）。
 */
export async function fetchEgressAssets({ outDir, log = console.log, now = () => new Date().toISOString() } = {}) {
  if (!outDir) {
    throw new Error('fetchEgressAssets 需要 outDir')
  }

  fs.mkdirSync(path.join(outDir, 'bin'), { recursive: true })

  const files = []
  const write = (name, buffer, source, version) => {
    const target = path.join(outDir, LAYOUT[name] || '', name)

    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, buffer)

    const digest = sha256(buffer)

    files.push({ name, bytes: buffer.length, sha256: digest, source, version })
    log(`[egress-fetch] 落位 ${name} → ${target}（${buffer.length} B, sha256 ${digest.slice(0, 12)}…）`)
  }

  const mihomoZip = await download(MIHOMO.url, log)

  checkBuffer({ name: 'mihomo zip', buffer: mihomoZip, bytes: MIHOMO.zipBytes, sha: MIHOMO.zipSha256, url: MIHOMO.url })

  const binary = readZipMember(mihomoZip, MIHOMO.member)

  checkBuffer({ name: MIHOMO.member, buffer: binary, bytes: MIHOMO.exeBytes, sha: MIHOMO.exeSha256 })
  write(MIHOMO.as, binary, MIHOMO.url, MIHOMO.version)

  const wintunZip = await download(WINTUN.url, log)

  checkBuffer({ name: 'wintun zip', buffer: wintunZip, sha: WINTUN.zipSha256, url: WINTUN.url })

  const driver = readZipMember(wintunZip, WINTUN.member)

  checkBuffer({ name: WINTUN.member, buffer: driver, bytes: WINTUN.dllBytes, sha: WINTUN.dllSha256 })
  write(WINTUN.as, driver, WINTUN.url, WINTUN.version)

  for (const geo of GEO) {
    const buffer = await download(geo.url, log)

    //: 这两件不带固定 sha：官方只有滚动的 latest，钉死就等于每次重建都红。如实记录实际值。
    checkBuffer({ name: geo.as, buffer, minBytes: geo.minBytes, url: geo.url })
    write(geo.as, buffer, geo.url, 'latest')
  }

  const manifest = {
    generated_at: now(),
    generator: 'apps/desktop/scripts/fetch-egress-assets.mjs',
    pinned: {
      mihomo: { version: MIHOMO.version, zip: MIHOMO.url, zip_sha256: MIHOMO.zipSha256 },
      wintun: { version: WINTUN.version, zip: WINTUN.url, zip_sha256: WINTUN.zipSha256 },
      geo: 'meta-rules-dat releases/download/latest（滚动，无历史 tag，故不钉 sha256）'
    },
    files
  }

  fs.writeFileSync(path.join(outDir, 'egress-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`)

  return manifest
}

const invokedDirectly = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href

if (invokedDirectly) {
  const args = process.argv.slice(2)
  const outIndex = args.indexOf('--out')
  const outDir = outIndex >= 0 ? args[outIndex + 1] : path.join('.egress-src', 'ship')

  fetchEgressAssets({ outDir })
    .then((manifest) => {
      console.log(`[egress-fetch] 齐了：${manifest.files.map((file) => file.name).join('、')}`)
    })
    .catch((error) => {
      console.error(`[egress-fetch] 失败，不出包：${error.message}`)
      process.exitCode = 1
    })
}

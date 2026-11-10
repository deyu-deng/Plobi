/**
 * 笔记里的本地图片：相对路径 → 盘上的绝对路径 → data URL。
 *
 * 为什么需要这一层：渲染进程跑在 app 的基址上（开发期是 `http://127.0.0.1:5174/`
 * 打包后是 `file:///…/dist/index.html`），Markdown 里的 `![](图.png)` 这种相对路径
 * 会被浏览器解析到那个基址底下 —— 盘上的文件根本不在那儿，于是**碎一张图**。
 * Obsidian 是拿「库根 + 相对路径」去解析的，我们照它的形状来。
 *
 * 落点的口径（用户 11-10 22:4x 拍板）：**附件放笔记同目录的附件夹，跟着笔记一起
 * git 提交**。所以一个引用按这个顺序找：
 *   ① 笔记同目录下的那个文件
 *   ② 笔记同目录的 `attachments/` 里
 *   ③ 笔记库根下
 *   ④ 笔记库根的 `attachments/` 里
 * 第一个读得到的算它。
 *
 * ⚠️ **不许越出笔记库根**：越界的引用一律不读（与后端 `notes_library` 同一口径）。
 * 路径是用户自己写的，但渲染进程拿着它去读盘 —— 逃出库根就是任意文件读。
 */

/** 本地引用在 Markdown 里走这个方案（见 `remark-note-images.ts` 的三条理由）。 */
export const ASSET_SCHEME = 'streamdown:'


/** 图片扩展名。Obsidian 那边的插图基本就这几种。 */
const IMAGE_EXT = /\.(?:png|jpe?g|gif|webp|svg|bmp|avif|ico)$/i

/** 是不是一个图片目标（按扩展名判，与 wikilink 插件同一套）。 */
export function isImageTarget(target: string): boolean {
  const cleaned = target.split(/[?#]/)[0].trim()

  return IMAGE_EXT.test(cleaned)
}

/** 目标是不是外链（http/https/data/blob）—— 那类不归我们管，直接给浏览器。 */
export function isRemoteAsset(src: string): boolean {
  return /^(?:https?:|data:|blob:)/i.test(src.trim())
}

/**
 * 把一个本地引用编成 `streamdown:` 后面那截。
 *
 * 用 base64url 而不是原样拼：路径里有中文、有空格、有 `#`／`?`，原样拼进
 * URL 会被各处再编码一次，最后解出来的东西就不是用户写的那串了。
 */
export function encodeAssetTarget(target: string): string {
  const bytes = new TextEncoder().encode(target)
  let binary = ''

  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }

  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** `encodeAssetTarget` 的逆。 */
export function decodeAssetTarget(encoded: string): string {
  const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/')
  const padding = (4 - (normalized.length % 4)) % 4
  const binary = atob(normalized + '='.repeat(padding))
  const bytes = Uint8Array.from(binary, char => char.charCodeAt(0))

  return new TextDecoder().decode(bytes)
}

/**
 * 从一个 `src` 里认出「本地引用」，认出就把原路径还回来。
 *
 * 不是本地引用（外链、别的方案）⇒ 返回 `null`，交给浏览器自己处理。
 */
export function decodeAssetSrc(src: string | undefined): string | null {
  if (!src || !src.startsWith(ASSET_SCHEME)) return null

  try {
    return decodeAssetTarget(src.slice(ASSET_SCHEME.length))
  } catch {
    return null
  }
}

const WINDOWS_ABSOLUTE = /^[A-Za-z]:[\\/]/
const UNC_PREFIX = /^\\\\/

/** 是不是绝对路径（盘符、UNC、正杠开头）。 */
export function isAbsolutePath(value: string): boolean {
  return WINDOWS_ABSOLUTE.test(value) || UNC_PREFIX.test(value) || value.startsWith('/')
}

/**
 * 拆路径：**根前缀与段分开**。
 *
 * 根前缀是 `/`（正杠开头）或盘符那一段（`D:`）—— 把它当普通段处理的话，
 * 一拼路径就把开头的 `/` 吃了，`/vault/notes` 变成 `vault/notes`，
 * 「在不在库根底下」这道判断也就跟着失效了。
 */
function splitPath(value: string): { root: string; segments: string[] } {
  const normalized = value.replace(/\\/g, '/')
  const segments = normalized.split('/').filter(segment => segment.length > 0)
  const head = segments[0] ?? ''

  if (/^[A-Za-z]:$/.test(head)) {
    return { root: `${head}/`, segments: segments.slice(1) }
  }

  return { root: normalized.startsWith('/') ? '/' : '', segments }
}

/** 拼回一条路径（正杠形态：Windows 的 fs 认，主进程那侧 `path.resolve` 也会收）。 */
function joinPath(root: string, segments: string[]): string {
  return `${root}${segments.join('/')}`
}

/**
 * 拼一条相对路径并把 `.` / `..` 收干净。
 *
 * 一律用正杠返回，不必在渲染层维护一套反斜杠规则。
 */
export function joinRelative(base: string, relative: string): string {
  const { root, segments } = splitPath(base)
  // 盘符那一段不许被 `..` 吃掉：吃掉了拼出来的就是相对路径，等于让主进程
  // 按 cwd 解析，那不是我们想要的位置。
  const floor = root && !/^[A-Za-z]:\/$/.test(root) ? 0 : segments.length > 0 ? 1 : 0
  const out = [...segments]

  for (const segment of splitPath(relative).segments) {
    if (segment === '.') continue
    if (segment === '..') {
      if (out.length > floor) out.pop()
      continue
    }
    out.push(segment)
  }

  return joinPath(root, out)
}

/** 候选路径是不是落在库根底下（比大小写不敏感 —— Windows 上盘符大小写无所谓）。 */
export function isInsideRoot(root: string, candidate: string): boolean {
  const rootPath = joinPath(splitPath(root).root, splitPath(root).segments).toLowerCase()
  const candidatePath = joinPath(splitPath(candidate).root, splitPath(candidate).segments).toLowerCase()

  if (!rootPath) return false

  return candidatePath === rootPath || candidatePath.startsWith(`${rootPath}/`)
}

/** 一篇笔记自己的目录（它的 `fsPath` 的上一层）。 */
export function noteBaseDir(fsPath: string | undefined): string | undefined {
  if (!fsPath) return undefined
  const { root, segments } = splitPath(fsPath)

  if (segments.length < 2) return undefined

  return joinPath(root, segments.slice(0, -1))
}

/**
 * 一个引用可能落在哪几个地方（按优先级排）。
 *
 * 只返回**落在库根之内**的候选 —— 越界的那几个在解析阶段就被剔掉了，
 * 不给读盘那一步任何机会。
 */
export function assetCandidates(rootDir: string | undefined, baseDir: string | undefined, target: string): string[] {
  const cleaned = target.split(/[?#]/)[0].trim()

  if (!cleaned) return []

  const out: string[] = []

  const push = (path: string | undefined) => {
    if (!path) return
    if (rootDir && !isInsideRoot(rootDir, path)) return
    if (!out.includes(path)) out.push(path)
  }

  if (isAbsolutePath(cleaned)) {
    push(cleaned.replace(/\\/g, '/'))

    return out
  }

  const relative = cleaned.replace(/^\.\//, '')

  if (baseDir) {
    push(joinRelative(baseDir, relative))
    push(joinRelative(joinRelative(baseDir, 'attachments'), relative))
  }

  if (rootDir) {
    push(joinRelative(rootDir, relative))
    push(joinRelative(joinRelative(rootDir, 'attachments'), relative))
  }

  return out
}

/** 读过的图缓存住 —— 一篇长笔记里同一张图会被引用很多次。 */
const dataUrlCache = new Map<string, string>()
const DATA_URL_CACHE_LIMIT = 64

async function readDataUrl(path: string): Promise<string | null> {
  const cached = dataUrlCache.get(path)

  if (cached !== undefined) return cached

  const bridge = window.plobiDesktop

  if (!bridge?.readFileDataUrl) return null

  try {
    const dataUrl = await bridge.readFileDataUrl(path)

    if (!dataUrl) return null

    if (dataUrlCache.size >= DATA_URL_CACHE_LIMIT) {
      const oldest = dataUrlCache.keys().next().value

      if (oldest !== undefined) dataUrlCache.delete(oldest)
    }
    dataUrlCache.set(path, dataUrl)

    return dataUrl
  } catch {
    // 读不到（不存在／不是图片／被敏感文件挡住）＝这一处候选不成立，
    // 继续试下一处；全都不成立就返回 null，由渲染层落回占位。
    return null
  }
}

/**
 * 把一个笔记里的图片引用读成 data URL。
 *
 * 返回 `null` ＝ 盘上真的没有这张图 —— 渲染层要**照实说**（落回占位），
 * 不能拿一张碎图标糊过去，也不能假装图还在。
 */
export async function loadNoteAssetDataUrl(
  rootDir: string | undefined,
  baseDir: string | undefined,
  target: string
): Promise<string | null> {
  for (const candidate of assetCandidates(rootDir, baseDir, target)) {
    const dataUrl = await readDataUrl(candidate)

    if (dataUrl) return dataUrl
  }

  return null
}

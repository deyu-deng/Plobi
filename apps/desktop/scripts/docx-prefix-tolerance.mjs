/**
 * docx 前缀容忍 —— 把非标准前缀（ns0:/ns1:…）在**读入时**改回惯用前缀（w:/mc:/a:…）。
 *
 * 为什么需要：有些写入方（.NET 的 OpenXML SDK、部分转换工具）序列化 WordprocessingML
 * 时会给每个主命名空间元素加前缀，于是文件里是 `<ns0:p>` 而不是 `<w:p>`。
 * GenOffice 的解析器按**字面**匹配 `'w:p'`、`'w:rFonts'`、`'w:drawing'`（没有命名空间
 * 解析），这类文件在它眼里等于一张空文档：段落样式全丢、字体全塌回默认、图片塌成
 * 「图片 N」占位、域塌成 `Field (ADDIN)`。Word / WPS 按命名空间 URI 解析，所以它们
 * 看同一个文件完全正常——前缀只是别名。
 *
 * 做法：只改**读**路径。`parseDocx` 里 `loadDocxZip` 之后包一层 `zip.file`，凡是
 * `word/*.xml` 的 `.async('string')` 结果先过一遍前缀重命名。写路径（保存）不碰——
 * 保存时 GenOffice 整档重写，输出自然是标准前缀，文档会自己变规范。
 *
 * 和 `bundle-genoffice.mjs` 里那段 `xlsxPrefixTolerancePlugin` 是同一个问题的两个格式：
 * 那一段剥前缀（SpreadsheetML 允许默认命名空间），这一段换前缀（WordprocessingML 混用
 * 多个命名空间，剥了会毁掉 `r:id` / `a:blip`）。
 */

/**
 * 标准 URI → 惯用前缀。解析器认这几个（`wp:` 它不认，但改名无害）。
 *
 * 放在函数内部而不是模块级：注入到 main.js 的是 `fn.toString()`，函数体必须自包含，
 * 否则搬过去会引用一个不存在的模块级常量。
 */
export function standardPrefixByUri() {
  return {
    'http://schemas.openxmlformats.org/wordprocessingml/2006/main': 'w',
    'http://schemas.openxmlformats.org/markup-compatibility/2006': 'mc',
    'http://schemas.openxmlformats.org/officeDocument/2006/relationships': 'r',
    'http://schemas.openxmlformats.org/drawingml/2006/main': 'a',
    'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing': 'wp',
    'http://schemas.openxmlformats.org/drawingml/2006/picture': 'pic',
    'http://schemas.microsoft.com/office/drawing/2010/main': 'a14',
    'http://schemas.microsoft.com/office/word/2010/wordml': 'w14',
    'http://schemas.microsoft.com/office/word/2012/wordml': 'w15',
    // VML（Word 97 图形，公式编辑器与旧对象都用它）与 Office 扩展命名空间。
    // 渲染器认 `v:`（4 处）与 `m:`（1 处）；`o:` / `w10:` / `wp14:` 它不看，
    // 但一并改名保持一致，免得 `mc:Ignorable` 里的清单对不上。
    'urn:schemas-microsoft-com:vml': 'v',
    'urn:schemas-microsoft-com:office:office': 'o',
    'urn:schemas-microsoft-com:office:word': 'w10',
    'http://schemas.openxmlformats.org/officeDocument/2006/math': 'm',
    'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing': 'wp14',
    // 空串 = 这个命名空间在 OOXML 里用**默认命名空间**（.rels 文件就是），
    // 前缀要整个剥掉而不是改名：`<ns0:Relationships>` → `<Relationships>`。
    'http://schemas.openxmlformats.org/package/2006/relationships': ''
  }
}

/**
 * 把一个 XML part 里的非标准前缀改回惯用前缀。已经是标准前缀时原样返回。
 *
 * 三条保护：
 * - 目标前缀已被别的 URI 占用时跳过（否则会把两个命名空间混成一个）
 * - `mc:Ignorable="ns2 ns10"` 那种前缀清单同步改名（漏了它 validate 会报 invalid prefix）
 * - 先改属性值清单、再改标签与声明（顺序反了旧前缀就找不到了）
 */
export function fixDocxPrefixes(text) {
  if (typeof text !== 'string' || !text.includes('xmlns:')) {
    return text
  }

  const standard = standardPrefixByUri()
  const declared = new Map()
  const decl = /xmlns:([A-Za-z_][\w.-]*)="([^"]+)"/g
  let match

  while ((match = decl.exec(text))) {
    declared.set(match[1], match[2])
  }

  const rename = new Map()

  declared.forEach((uri, prefix) => {
    const want = standard[uri]

    if (want === undefined || want === prefix) {
      return
    }

    // 目标前缀非空时不能撞车；空串是默认命名空间，没有撞车问题。
    if (want && declared.has(want)) {
      return
    }

    rename.set(prefix, want)
  })

  if (rename.size === 0) {
    return text
  }

  let out = text.replace(/(\bIgnorable=")([^"]*)(")/g, (_all, head, list, tail) => {
    const mapped = list
      .split(/\s+/)
      .map(token => rename.get(token) ?? token)
      .join(' ')

    return `${head}${mapped}${tail}`
  })

  // 长前缀先换，避免 ns1 抢在 ns10 前面。
  for (const old of [...rename.keys()].sort((a, b) => b.length - a.length)) {
    const want = rename.get(old)
    const escaped = old.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

    if (want === '') {
      // 默认命名空间：剥掉前缀，连 xmlns 声明本身也退回无前缀形式。
      out = out
        .replace(new RegExp(`xmlns:${escaped}=`, 'g'), 'xmlns=')
        .replace(new RegExp(`<${escaped}:`, 'g'), '<')
        .replace(new RegExp(`</${escaped}:`, 'g'), '</')
        .replace(new RegExp(`\\s${escaped}:`, 'g'), ' ')

      continue
    }

    out = out
      .replace(new RegExp(`xmlns:${escaped}=`, 'g'), `xmlns:${want}=`)
      .replace(new RegExp(`<${escaped}:`, 'g'), `<${want}:`)
      .replace(new RegExp(`</${escaped}:`, 'g'), `</${want}:`)
      .replace(new RegExp(`\\s${escaped}:`, 'g'), ` ${want}:`)
  }

  return out
}

/** 给一个 JSZip 实例包一层：`word/*.xml` 读成字符串时先过前缀重命名。 */
export function normalizeDocxZip(zip) {
  const readFile = zip.file.bind(zip)

  zip.file = (name, ...rest) => {
    const entry = readFile(name, ...rest)

    if (
      !entry ||
      typeof entry.async !== 'function' ||
      typeof name !== 'string' ||
      // `.rels` 也要管：`word/_rels/document.xml.rels` 里同样是非标准前缀，
      // 而它决定 `r:embed` / `r:id` 能不能解析出图片——漏了它图片就全挂。
      !/\.(xml|rels)$/iu.test(name)
    ) {
      return entry
    }

    const read = entry.async.bind(entry)

    entry.async = type =>
      read(type).then(value => (type === 'string' ? fixDocxPrefixes(value) : value))

    return entry
  }
}

/**
 * 注入进 main.js 的运行时源码。
 *
 * 用 `fn.toString()` 搬函数体（单一真源），但必须做两件事：
 * 1. 去掉具名声明（`function foo` → `function`），因为搬过去要赋给 `__plobi*` 变量
 * 2. 把函数之间的互相引用改成搬过去之后的新名字，否则运行时找不到
 */
function inlineFunction(fn, newName, refs = {}) {
  let source = fn.toString().replace(/^function\s+\w+/u, 'function')

  for (const [from, to] of Object.entries(refs)) {
    source = source.replace(new RegExp(`\\b${from}\\b`, 'gu'), to)
  }

  return `const ${newName} = ${source};`
}

const RUNTIME_SOURCE = [
  inlineFunction(standardPrefixByUri, '__plobiStandardPrefixByUri'),
  inlineFunction(fixDocxPrefixes, '__plobiFixDocxPrefixes', {
    standardPrefixByUri: '__plobiStandardPrefixByUri'
  }),
  inlineFunction(normalizeDocxZip, '__plobiNormalizeDocxZip', {
    fixDocxPrefixes: '__plobiFixDocxPrefixes'
  })
].join('\n\n')

// 不带分号：vendor 的 TS 源码没分号，打包后的 main.js 有——前缀相同，两种都能命中，
// 替换后剩下那个分号正好收尾。
const NEEDLE = 'const zip = await loadDocxZip(bytes)'

/**
 * 在 GenOffice 的 `dist/main.js` 里给 `parseDocx` 插一行归一化调用。
 *
 * 找不到 needle 或找到多处就**抛错**——GenOffice 升级改了这段结构时必须让构建失败，
 * 不能静默不生效（照 `bundle-genoffice.mjs` 里 xlsx 那段 patch 的规矩）。
 */
export function patchDocxParser(source) {
  const occurrences = source.split(NEEDLE).length - 1

  if (occurrences !== 1) {
    throw new Error(
      `docx prefix patch: expected exactly one \`${NEEDLE}\` in opendesk-office dist/main.js, found ${occurrences}. ` +
        'The upstream parseDocx shape changed — re-check the injection point before rebuilding.'
    )
  }

  return (
    source.replace(NEEDLE, `${NEEDLE}\n  __plobiNormalizeDocxZip(zip);`) +
    `\n\n// ── Plobi: docx 前缀容忍（见 apps/desktop/scripts/docx-prefix-tolerance.mjs）──\n${RUNTIME_SOURCE}\n`
  )
}

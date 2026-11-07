/**
 * 首装与导入这一批新句子（`WP-SOURCE-FIRST-RUN-IMPORT`）。
 *
 * **为什么不住 i18n 五档**：那五档此刻被四条在派线同时认领（`WP-I18N-DEDUP` 等），
 * 按「被占文件只留挂载、逻辑进本线新模块」的口径，新句子先住这里、按当前语言取；
 * 等那几条线签完再并回目录（并回是 `WP-I18N-DEDUP` 的活，不在本格）。
 *
 * 句子只写"这是什么"，不写机制（屏幕文案纪律）：加号那颗的 accessible name 是「配置 X」，
 * 导入按钮的提示只列支持的扩展名，不解释解析器怎么工作。
 */

export interface FirstRunLabels {
  /** 加号那颗按钮的 accessible name 模板，`{name}` 换成信源名。 */
  configure: string
  /** 行上"还没配过"那一格的人话。 */
  notConfigured: string
  /** Files 组里那颗按钮。 */
  importFiles: string
  /** 按钮下面那行小字：支持哪几种。 */
  importHint: string
  /** 导入成功的通知，`{n}` 换成进池条数。 */
  importDone: string
  /** 导入失败的通知前缀（后面接后端那句为什么）。 */
  importFailed: string
}

const EN: FirstRunLabels = {
  configure: 'Set up {name}',
  notConfigured: 'Not set up',
  importFiles: 'Import files',
  importHint: '.ics · .csv · .md · .txt · .opml',
  importDone: '{n} items added to the pool',
  importFailed: 'Import failed',
}

const ZH: FirstRunLabels = {
  configure: '配置 {name}',
  notConfigured: '未配置',
  importFiles: '导入文件',
  importHint: '.ics · .csv · .md · .txt · .opml',
  importDone: '{n} 条进池',
  importFailed: '导入没成',
}

const ZH_HANT: FirstRunLabels = {
  configure: '設定 {name}',
  notConfigured: '未設定',
  importFiles: '匯入檔案',
  importHint: '.ics · .csv · .md · .txt · .opml',
  importDone: '{n} 條進池',
  importFailed: '匯入失敗',
}

const JA: FirstRunLabels = {
  configure: '{name} を設定',
  notConfigured: '未設定',
  importFiles: 'ファイルを読み込む',
  importHint: '.ics · .csv · .md · .txt · .opml',
  importDone: '{n} 件をプールに追加',
  importFailed: '読み込みに失敗',
}

export function firstRunLabels(locale: string): FirstRunLabels {
  // 读不到语言（测试里的半份 mock、或配置还没落到）就回英文，不抛——一颗按钮不该把整屏崩掉。
  const lang = String(locale || 'en')

  if (lang.startsWith('zh')) {
    return lang.includes('hant') || lang.includes('TW') || lang.includes('HK') ? ZH_HANT : ZH
  }

  if (lang.startsWith('ja')) {
    return JA
  }

  return EN
}

/**
 * 首次配置那一块的**引导句**（`WP-SOURCE-FIRST-RUN-IMPORT` 11-08 补做那一刀）。
 *
 * **为什么不住 i18n 五档**：那五档此刻被几条在派线同时认领，按「被占文件只留挂载、
 * 逻辑进本线新模块」的口径，新句子先住这里、按当前语言取（与 `./first-run-labels` 同一手）。
 *
 * 句子只写"这是什么"与"你要做哪一步"，不写机制（屏幕文案纪律）：
 * 一行 ≤ 20 字；机器词（`ok` / 状态码 / provider slug）不上屏。
 *
 * **键按信源 id 分**：与 `agenda.sources.<source>`（专名）、`settings.sources.fields.<key>`
 * （字段名）同一套路——声明给缺省值，语言目录优先。查不到就退回声明里的英文缺省值，
 * 第三方包零改动也能落在屏上（只是没翻译）。
 */

export interface SetupGuideLabels {
  /** 那颗按钮上写什么，按信源 id 查。查不到 ⇒ 退回声明里的 `action`。 */
  actions: Record<string, string>
  /** 卡上那一句指路，按信源 id 查。查不到 ⇒ 不画那一行。 */
  details: Record<string, string>
  /** 查不到任何一份时那张卡的头。 */
  fallbackTitle: string
  /** 打开下载页失败时那一句。 */
  openFailed: string
  /** 「检查一下」跑完了那一句（后端没给话时的兜底）。 */
  checkDone: string
  /** 「检查一下」没跑成那一句。 */
  checkFailed: string
  /** 「检查一下」那颗——用户做完那一步之后，产品重新看一眼。 */
  recheck: string
  /** 那张卡的头，按信源 id 查。查不到 ⇒ `fallbackTitle`。 */
  titles: Record<string, string>
}

const EN: SetupGuideLabels = {
  actions: {
    dingtalk: 'Download DingTalk',
    mubu: 'Download Mubu',
    wechat: 'Download ChatLog'
  },
  details: {
    dingtalk: 'Install it and sign in, then check again.',
    mubu: 'Install it and sign in, then check again.',
    wechat: 'Install it and sign in to WeChat, then check again.'
  },
  checkDone: 'Checked',
  checkFailed: 'Could not check',
  fallbackTitle: 'Set this source up',
  openFailed: 'Could not open the download page',
  recheck: 'Check again',
  titles: {
    dingtalk: 'DingTalk desktop app',
    mubu: 'Mubu desktop app',
    wechat: 'WeChat message collector'
  }
}

const ZH: SetupGuideLabels = {
  actions: {
    dingtalk: '下载钉钉',
    mubu: '下载幕布',
    wechat: '下载 ChatLog'
  },
  details: {
    dingtalk: '装好后登录一次，再点「检查一下」。',
    mubu: '装好后登录一次，再点「检查一下」。',
    wechat: '装好后登录微信，再点「检查一下」。'
  },
  checkDone: '检查过了',
  checkFailed: '没查成',
  fallbackTitle: '配置这条信源',
  openFailed: '下载页没打开',
  recheck: '检查一下',
  titles: {
    dingtalk: '钉钉桌面客户端',
    mubu: '幕布桌面客户端',
    wechat: '微信消息采集服务'
  }
}

const ZH_HANT: SetupGuideLabels = {
  actions: {
    dingtalk: '下載釘釘',
    mubu: '下載幕布',
    wechat: '下載 ChatLog'
  },
  details: {
    dingtalk: '裝好後登入一次，再點「檢查一下」。',
    mubu: '裝好後登入一次，再點「檢查一下」。',
    wechat: '裝好後登入微信，再點「檢查一下」。'
  },
  checkDone: '檢查過了',
  checkFailed: '沒查成',
  fallbackTitle: '設定這條信源',
  openFailed: '下載頁沒開啟',
  recheck: '檢查一下',
  titles: {
    dingtalk: '釘釘桌面客戶端',
    mubu: '幕布桌面客戶端',
    wechat: '微信訊息採集服務'
  }
}

const JA: SetupGuideLabels = {
  actions: {
    dingtalk: 'DingTalk をダウンロード',
    mubu: 'Mubu をダウンロード',
    wechat: 'ChatLog をダウンロード'
  },
  details: {
    dingtalk: 'インストールしてサインインし、もう一度確認してください。',
    mubu: 'インストールしてサインインし、もう一度確認してください。',
    wechat: 'インストールして WeChat にサインインし、もう一度確認してください。'
  },
  checkDone: '確認しました',
  checkFailed: '確認できませんでした',
  fallbackTitle: 'このソースを設定',
  openFailed: 'ダウンロードページを開けませんでした',
  recheck: 'もう一度確認',
  titles: {
    dingtalk: 'DingTalk デスクトップアプリ',
    mubu: 'Mubu デスクトップアプリ',
    wechat: 'WeChat メッセージコレクター'
  }
}

export function setupGuideLabels(locale: string): SetupGuideLabels {
  // 读不到语言（测试里的半份 mock、或配置还没落到）就回英文，不抛——一张卡不该把整屏崩掉。
  const lang = String(locale || 'en')

  if (lang.startsWith('zh')) {
    return lang.includes('hant') || lang.includes('TW') || lang.includes('HK') ? ZH_HANT : ZH
  }

  if (lang.startsWith('ja')) {
    return JA
  }

  return EN
}

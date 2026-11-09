import { beforeEach, describe, expect, it } from 'vitest'

import {
  $cardFocus,
  $dossierPanelMode,
  $dossierPanelVisible,
  setCardFocus,
  toggleDossierPanel
} from './layout'

/**
 * 档案卡那张「浮在对话右上角的卡」什么时候露脸。
 *
 * 这组判据钉的是**行为**，不是源码文字。11-09 现场出过一条典型的「文字钉住了、行为没钉住」：
 * `computed([a, b], cb)` 在 nanostores 1.x 里是 `cb(a, b)`（按参数展开），
 * 写成 `([mode, focus]) => …` 会把 `'auto'` 这个**字符串**切成一两个字，
 * 三档全不匹配 ⇒ 这张卡**恒为不露脸**：点抬头那枚图标开不了（用户 11-09 08:47
 * 「档案点不开呀」），对话卡全屏也不默认显示（「全屏也不默认显示」）。
 * 所以这里必须**真跑一遍**那三档，不能只读源码。
 */
describe('档案卡的显隐口径', () => {
  beforeEach(() => {
    $dossierPanelMode.set('auto')
    $cardFocus.set(null)
  })

  it('auto 档跟着对话卡全屏走：全屏露脸，工作区在显示时收起', () => {
    expect($dossierPanelVisible.get()).toBe(false)

    setCardFocus('chat')
    expect($dossierPanelVisible.get()).toBe(true)

    setCardFocus('workbench')
    expect($dossierPanelVisible.get()).toBe(false)
  })

  it('点它才展开：点一下露脸，再点一下收回去', () => {
    expect($dossierPanelVisible.get()).toBe(false)

    toggleDossierPanel()
    expect($dossierPanelVisible.get()).toBe(true)

    toggleDossierPanel()
    expect($dossierPanelVisible.get()).toBe(false)
  })

  it('点过的那一次只在这一面算数：换了面回到默认（全屏又默认露脸）', () => {
    // 全屏里手动关掉 ⇒ 这一面不露脸。
    setCardFocus('chat')
    toggleDossierPanel()
    expect($dossierPanelVisible.get()).toBe(false)

    // 换到工作区那面，再换回全屏 ⇒ 回到**默认**（默认显示）。
    setCardFocus('workbench')
    setCardFocus('chat')
    expect($dossierPanelVisible.get()).toBe(true)
  })

  it('工作区那面点开档案卡，它就露脸（默认收起，点它才展开）', () => {
    setCardFocus('workbench')
    expect($dossierPanelVisible.get()).toBe(false)

    toggleDossierPanel()
    expect($dossierPanelVisible.get()).toBe(true)
  })
})

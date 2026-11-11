import { visit } from 'unist-util-visit'

import { encodeAssetTarget, isRemoteAsset } from './note-media'

/**
 * remark 插件：把**本地图片**引用改写成 `streamdown:` 方案。
 *
 * 为什么非改不可：Streamdown 默认的 rehype 链里带着 `rehype-harden`，它对
 * 图片地址的处理是：
 *   - `图.png`（裸相对名）⇒ 判成「不是 URL」，**src 直接被掐掉**；
 *   - `./图.png` ⇒ 被改成 `/图.png`（站点根相对）。
 * 两种在笔记栏里都是碎图——浏览器那一侧根本不该管这件事，盘上的文件在哪，
 * 只有笔记栏知道。
 *
 * 所以在 AST 层把「本地引用」挑出来，编成 `streamdown:<base64url>`：
 *   ① `rehype-sanitize` 的 schema 明确放行 `streamdown:` 这个 src 方案
 *      （Streamdown 自己加进去的那一条）；
 *   ② `rehype-harden` 对非 http/https 的方案原样放行（`allowedProtocols: ['*']`）；
 *   ③ 渲染层认出它 ⇒ 按「笔记同目录 → 同目录的 attachments/ → 库根」去盘上读。
 *
 * 外链（http/https/data/blob）不动：那些本来就该交给浏览器。
 */
export function remarkNoteImages() {
  return (tree: unknown): void => {
    visit(tree as never, 'image', (node: { url?: string }) => {
      const url = node.url

      if (!url || isRemoteAsset(url)) {return}

      if (url.startsWith('streamdown:')) {return}

      node.url = `streamdown:${encodeAssetTarget(url)}`
    })
  }
}

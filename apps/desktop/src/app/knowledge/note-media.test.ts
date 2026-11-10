import { describe, expect, it } from 'vitest'

import {
  assetCandidates,
  decodeAssetSrc,
  decodeAssetTarget,
  encodeAssetTarget,
  isAbsolutePath,
  isImageTarget,
  isInsideRoot,
  isRemoteAsset,
  joinRelative,
  noteBaseDir
} from './note-media'

/**
 * 图片落点的口径（用户 11-10 22:4x 拍板）：**附件放笔记同目录的附件夹，
 * 跟着笔记一起 git 提交**。所以一个引用的查找顺序是固定的：
 * 笔记同目录 → 同目录的 `attachments/` → 库根 → 库根的 `attachments/`。
 *
 * 另外一条硬边界：**不许越出笔记库根**——越界的引用一律不读。
 */
const ROOT = '/vault/notes'
const BASE = '/vault/notes/物理/大学物理'

describe('note-media 路径解析', () => {
  it('按「同目录 → attachments/ → 库根」的顺序给候选', () => {
    expect(assetCandidates(ROOT, BASE, '图.png')).toEqual([
      '/vault/notes/物理/大学物理/图.png',
      '/vault/notes/物理/大学物理/attachments/图.png',
      '/vault/notes/图.png',
      '/vault/notes/attachments/图.png'
    ])
  })

  it('引用里带子目录时按同一条规则找', () => {
    const candidates = assetCandidates(ROOT, BASE, 'assets/图.png')

    expect(candidates[0]).toBe('/vault/notes/物理/大学物理/assets/图.png')
    expect(candidates[1]).toBe('/vault/notes/物理/大学物理/attachments/assets/图.png')
  })

  it('`..` 越出库根的引用被剔掉（渲染进程拿着它去读盘，不能逃出去）', () => {
    // 库根是 `/vault/notes`，这篇笔记在 `/vault/notes/物理/大学物理`（深两层）。
    // 往上跳四层才真的逃出库根。
    expect(assetCandidates(ROOT, BASE, '../../../../etc/passwd')).toEqual([])
  })

  it('还在库根之内的 `..` 放行（Obsidian 的库内相对引用是合法的）', () => {
    expect(assetCandidates(ROOT, BASE, '../../微积分/图.png')[0]).toBe('/vault/notes/微积分/图.png')
  })

  it('库根之外的绝对路径也不给', () => {
    expect(assetCandidates(ROOT, BASE, 'C:/Windows/System32/config/SAM')).toEqual([])
  })

  it('绝对路径在库根之内时照用', () => {
    expect(assetCandidates(ROOT, BASE, '/vault/notes/物理/图.png')).toEqual(['/vault/notes/物理/图.png'])
  })

  it('反斜杠当分隔符处理（Windows 上写出来的路径）', () => {
    expect(assetCandidates(ROOT, BASE, 'assets\\图.png')[0]).toBe('/vault/notes/物理/大学物理/assets/图.png')
  })

  it('`joinRelative` 收干净 `.` 与 `..`，但不吃掉盘符', () => {
    expect(joinRelative('D:/vault/notes', '../图.png')).toBe('D:/vault/图.png')
    expect(joinRelative('D:/vault/notes', './图.png')).toBe('D:/vault/notes/图.png')
  })

  it('`isInsideRoot` 比大小写不敏感', () => {
    expect(isInsideRoot('/Vault/Notes', '/vault/notes/物理/x.md')).toBe(true)
    expect(isInsideRoot('/vault/notes', '/vault/notesx/x.md')).toBe(false)
  })

  it('`noteBaseDir` 取的是笔记自己那一层', () => {
    expect(noteBaseDir('/vault/notes/物理/大学物理.md')).toBe('/vault/notes/物理')
    expect(noteBaseDir(undefined)).toBeUndefined()
  })
})

describe('note-media 判定', () => {
  it('按扩展名认图片', () => {
    expect(isImageTarget('图.png')).toBe(true)
    expect(isImageTarget('attachments/图.JPEG')).toBe(true)
    expect(isImageTarget('向量图.svg')).toBe(true)
    expect(isImageTarget('微积分.md')).toBe(false)
  })

  it('外链不归我们管', () => {
    expect(isRemoteAsset('https://example.com/a.png')).toBe(true)
    expect(isRemoteAsset('data:image/png;base64,AA')).toBe(true)
    expect(isRemoteAsset('图.png')).toBe(false)
  })

  it('绝对路径认盘符、UNC 与正杠', () => {
    expect(isAbsolutePath('C:\\vault')).toBe(true)
    expect(isAbsolutePath('\\\\server\\share')).toBe(true)
    expect(isAbsolutePath('/vault')).toBe(true)
    expect(isAbsolutePath('图.png')).toBe(false)
  })
})

describe('note-media 本地引用编码', () => {
  it('中文与空格的引用编解码后一字不差', () => {
    const target = '物理 图/向量 图.png?x=1#y'

    expect(decodeAssetTarget(encodeAssetTarget(target))).toBe(target)
  })

  it('编出来的是纯 ASCII（URL 里不会被再编码一次）', () => {
    expect(encodeAssetTarget('图.png')).toMatch(/^[A-Za-z0-9_-]+$/)
  })

  it('`decodeAssetSrc` 只认本地引用，外链还给 null', () => {
    expect(decodeAssetSrc(`streamdown:${encodeAssetTarget('图.png')}`)).toBe('图.png')
    expect(decodeAssetSrc('https://example.com/a.png')).toBeNull()
    expect(decodeAssetSrc(undefined)).toBeNull()
  })
})

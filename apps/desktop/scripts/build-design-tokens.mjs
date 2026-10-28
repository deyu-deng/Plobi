/**
 * 设计语言令牌构建（`npm run design:tokens`）。
 *
 * 真源是 `design/*.tokens.json`：`primitives`（原始值）+ `layout`（结构与密度：栅格/行高/控件/
 * 面板宽——这是**设计语言**的骨架）+ `semantic.{light,dark}`（语义色，**只是一套参照主题**：
 * 颜色归 `themes/` 那套运行时主题引擎管，用户可以换，所以不许把某个色板当语言本身）。
 * 产物是 `src/design-tokens.css`（**生成物，不许手改**）。
 *
 * 两条硬约束：
 * 1. **命名前缀 `--dl-`**：与现有 192 条 `--ui-*` 并存，迁移期谁都不破；等某屏迁完，
 *    再把那一屏的 `--ui-*` 改成引用 `--dl-*`，最后才删旧名。
 * 2. **选择器跟着应用走**：`:root` 是亮色、`.dark` 是暗色（这台桌面现在就是这么切的）。
 */
import StyleDictionary from 'style-dictionary'

const dark = new StyleDictionary({
  source: [
    'design/primitives.tokens.json',
    'design/layout.tokens.json',
    'design/semantic.dark.tokens.json',
  ],
  platforms: {
    css: {
      transformGroup: 'css',
      prefix: 'dl',
      buildPath: 'src/',
      files: [
        {
          destination: 'design-tokens.dark.css',
          format: 'css/variables',
          options: { selector: '.dark', outputReferences: true },
        },
      ],
    },
  },
})

const light = new StyleDictionary({
  source: [
    'design/primitives.tokens.json',
    'design/layout.tokens.json',
    'design/semantic.light.tokens.json',
  ],
  platforms: {
    css: {
      transformGroup: 'css',
      prefix: 'dl',
      buildPath: 'src/',
      files: [
        {
          destination: 'design-tokens.light.css',
          format: 'css/variables',
          options: { selector: ':root', outputReferences: true },
        },
      ],
    },
  },
})

await dark.buildAllPlatforms()
await light.buildAllPlatforms()
console.log('设计令牌已生成：src/design-tokens.light.css（:root）／src/design-tokens.dark.css（.dark）')

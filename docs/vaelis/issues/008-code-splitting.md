---
id: 008
title: 前端代码分割与 Bundle 体积优化
status: ready-for-agent
priority: P2
phase: 1
---

## 问题

`npm run build` 警告：
```
dist/assets/index-XfMqu6ku.js  1,543.31 kB │ gzip: 445.88 kB
(!) Some chunks are larger than 500 kB after minification
```

主 bundle 1.54MB（gzip 后 446KB）意味着：
1. 首次加载慢（3G 网络下 > 2 秒）
2. 内存占用高
3. 不符合 Blueprint "绝对不能接受的"性能要求

## 根因分析

- 所有页面组件在 `App.tsx` 中静态导入
- Shiki 语言高亮支持全部 100+ 语言，全部打包进主 chunk
- 可能还有其他重型库（Three.js、AlphaTab）被静态引入

## 目标

将主 bundle 降至 < 500KB（gzip 后 < 150KB），重型组件按需加载。

## 验收标准

- [ ] 使用 `React.lazy()` + `Suspense` 按路由分割代码
- [ ] Shiki 语言高亮改为按需加载（或仅打包常用语言）
- [ ] `npm run build` 后无 > 500KB chunk 警告
- [ ] 主 bundle gzip 后 < 150KB
- [ ] 分割后的 chunk 加载有 fallback UI（避免白屏）

## 技术方案

```tsx
// App.tsx 改造
const FocusMode = lazy(() => import("./layouts/FocusMode"));
const ResourceManager = lazy(() => import("./components/resources/ResourceManager"));

// 使用 Suspense fallback
<Suspense fallback={<LoadingSpinner />}>
  <FocusMode />
</Suspense>
```

Shiki 按需加载：
```ts
import { getHighlighter } from "shiki";
// 仅加载常用语言
const highlighter = await getHighlighter({ themes: ["dark-plus"], langs: ["ts", "tsx", "py", "md", "json"] });
```

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | 无 |
| API | 无 |
| UI | `App.tsx` 路由改造、`MessageBubble.tsx` Shiki 加载改造 |
| Test | 构建产物大小断言（可选） |

## 预估工作量

2-3 小时

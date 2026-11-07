// 应用图标的候选路径表（纯函数，可单测）。
//
// 为什么要有这一层：Windows 的**窗口**图标不吃 PNG。喂 PNG 会被静默忽略，任务栏
// 回落到 electron.exe 内嵌的默认原子——用户 11-07 的任务栏截图实锤过这个形状
// （换图之前它也不是旧头像，是 Electron 默认）。所以 win32 必须优先 .ico：
// 源码态在 assets/icon.ico，装机态由 extraResources 拷到 resourcesPath/icon.ico。
// macOS / Linux 的窗口与 Dock 吃 PNG，保持原顺序。
//
// 顺序即优先级：调用方拿第一个**存在**的。托盘与窗口共用这张表。

export interface AppIconPathInputs {
  isWindows: boolean
  appRoot: string
  resourcesPath?: string | null
  unpackedRoot: string
  join: (...parts: string[]) => string
}

export function appIconPaths({
  isWindows,
  appRoot,
  resourcesPath,
  unpackedRoot,
  join
}: AppIconPathInputs): string[] {
  const windowsIco = isWindows
    ? [
        join(appRoot, 'assets', 'icon.ico'),
        ...(resourcesPath ? [join(resourcesPath, 'icon.ico')] : [])
      ]
    : []
  return [
    ...windowsIco,
    join(appRoot, 'public', 'apple-touch-icon.png'),
    join(appRoot, 'dist', 'apple-touch-icon.png'),
    join(unpackedRoot, 'dist', 'apple-touch-icon.png')
  ]
}

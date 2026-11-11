// 应用图标的候选路径表（纯函数，可单测）。
//
// 为什么要有这一层：Windows 的**窗口**图标不吃 PNG。喂 PNG 会被静默忽略，任务栏
// 回落到 electron.exe 内嵌的默认原子——用户 11-07 的任务栏截图实锤过这个形状
// （换图之前它也不是旧头像，是 Electron 默认）。所以 win32 必须优先 .ico：
// 源码态在 assets/icon.ico，装机态由 extraResources 拷到 resourcesPath/icon.ico。
// macOS / Linux 的窗口与 Dock 吃 PNG，保持原顺序。
//
// 顺序即优先级：调用方拿第一个**存在**的。托盘与窗口共用这张表。

/**
 * macOS 菜单栏（status bar）图标的候选路径表。
 *
 * 关键差别：菜单栏不吃彩色应用图标。Apple 的规矩是 template image —— 只用
 * **黑色 + alpha**，由系统按亮/暗色自动反相。所以这里指向 `assets/trayTemplate*.png`
 * （纯黑剪影、透明底、18×18pt），绝不回落到 apple-touch-icon.png：那张 180px
 * 满幅彩图在菜单栏里会被拉成一块带底的彩色方块，比旁边所有图标都大一圈。
 *
 * `badge` 变体是同一剪影 + 右下角一枚黑点（有出口异常时才换上去），因为 template
 * 语义下彩色角标画不出对比度，颜色区分只能让位给「有点/没点」。
 */
export type TrayIconKind = 'base' | 'badge'

export interface TrayIconPathInputs {
  appRoot: string
  resourcesPath?: string | null
  join: (...parts: string[]) => string
}

export function trayIconPaths(
  { appRoot, resourcesPath, join }: TrayIconPathInputs,
  kind: TrayIconKind
): string[] {
  const file = kind === 'badge' ? 'trayTemplateBadge.png' : 'trayTemplate.png'

  return [
    join(appRoot, 'assets', file),
    ...(resourcesPath ? [join(resourcesPath, file)] : [])
  ]
}

export interface AppIconPathInputs {
  // 'darwin' | 'win32' | 'linux' | 其它；纯字符串以免在单测里依赖 NodeJS 命名空间
  platform: string
  appRoot: string
  resourcesPath?: string | null
  unpackedRoot: string
  join: (...parts: string[]) => string
}

export function appIconPaths({
  platform,
  appRoot,
  resourcesPath,
  unpackedRoot,
  join
}: AppIconPathInputs): string[] {
  const isWindows = platform === 'win32'
  const isMac = platform === 'darwin'

  const windowsIco = isWindows
    ? [
        join(appRoot, 'assets', 'icon.ico'),
        ...(resourcesPath ? [join(resourcesPath, 'icon.ico')] : [])
      ]
    : []

  // macOS：开发态 `app.dock.setIcon()` 拿到的图会被**直接铺满** 48pt 方框，既没有
  // 留白也没有圆角。`apple-touch-icon.png` 是 iOS 的满幅设计（180px，四角全是实色
  // #cd6b43），喂给 Dock 就得到一个直角大块——用户 11-07 与 11-11 两次截图指的都
  // 是这个形状。所以 macOS 首选项是 `assets/icon-macos.png`：同一张母版按 Apple 规范
  // 重排的 1024 画布（图形 824 居中 + 185 圆角 + 轻投影），已入库；
  // scripts/make-platform-icons.py 不重新生成它，避免把那版验收过的外观改掉。
  // touch icon 退为最后兜底，不再抢首位。
  const macIcon = isMac
    ? [
        join(appRoot, 'assets', 'icon-macos.png'),
        ...(resourcesPath ? [join(resourcesPath, 'icon-macos.png')] : [])
      ]
    : []

  // Linux：freedesktop 的惯例恰好相反——`.desktop` 里的图标由桌面环境自己按主题
  // 裁剪，图本身要**满幅**，自带留白反而会显得比旁边一排小一圈。所以这里用满幅的
  // `assets/icon-linux.png`（1024，满幅 + 圆角，scripts/make-platform-icons.py 出的），
  // 跟 macOS 那套 824/1024 不通用。别把 icon-macos.png 塞给 Linux：那是给 Apple 的
  // 留白缩进，GNOME/KDE 不会替你补回来。
  const linuxIcon = !isWindows && !isMac
    ? [
        join(appRoot, 'assets', 'icon-linux.png'),
        ...(resourcesPath ? [join(resourcesPath, 'icon-linux.png')] : [])
      ]
    : []

  return [
    ...windowsIco,
    ...macIcon,
    ...linuxIcon,
    join(appRoot, 'public', 'apple-touch-icon.png'),
    join(appRoot, 'dist', 'apple-touch-icon.png'),
    join(unpackedRoot, 'dist', 'apple-touch-icon.png')
  ]
}

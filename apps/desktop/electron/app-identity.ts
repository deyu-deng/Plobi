// 应用身份（Windows 的 AUMID）与通知点击该做什么——纯函数，可单测。
//
// 为什么要有这一层（2026-11-08 用户截图实锤）：
// 点通知时，Windows 是**按 AUMID 唤起「登记在这个 AUMID 上的可执行文件」**。
// 开发态跑的是 dev 树的 electron.exe（不是产品 exe）。它若也占着产品那个
// AUMID（com.plobi.desktop），系统唤起的就是**不带 app 路径**的 electron.exe
// ⇒ Electron 打开自己那页默认窗口（「To run a local app…」），标题栏与任务栏
// 图标都是 Electron 的。装机版与开发态抢同一个身份，点通知就可能唤起错的那个。
//
// 判据用 `process.defaultApp`（Electron 自己标的：我是被 electron.exe 带路径
// 启动的），**不用** `app.isPackaged`——生产 bundle 会把 packaged=true 烤进去
// （scripts/bundle-electron-main.mjs），源码态跑生产 bundle 时 app.isPackaged
// 也是 true，判不出「这不是产品 exe」。

export interface AppUserModelIdInputs {
  /** Windows 才有 AUMID 这回事；macOS/Linux 不需要。 */
  isWindows: boolean
  /** Electron 的 process.defaultApp：被 electron.exe 带路径启动 ⇒ 不是产品 exe。 */
  isDefaultApp: boolean
  /** 产品 AUMID，与 package.json 的 build.appId 一致。 */
  productAumid: string
}

/**
 * 该占用哪个 AUMID；返回 null = 不要动（用 Electron/系统自己的）。
 *
 * 装机态：占产品那个（与开始菜单快捷方式上的 AUMID 一致 ⇒ 通知点击能唤起我们）。
 * 开发态：不占——开发态那个可执行文件是 electron.exe，占了产品身份反而会让
 *         系统唤起一个「没有 app 路径」的空 Electron。
 */
export function resolveAppUserModelId({
  isWindows,
  isDefaultApp,
  productAumid
}: AppUserModelIdInputs): string | null {
  if (!isWindows) {
    return null
  }
  if (isDefaultApp) {
    return null
  }
  return productAumid
}

/** 点通知时应用该怎么回应。 */
export type NotificationActivation = 'focus' | 'reopen'

/**
 * 窗口还在（可见或收在托盘里）⇒ 把它叫到前面来；
 * 窗口已经没了（正在退出）⇒ 重开一扇——用户点了通知却什么都没发生，
 * 比什么都不做更糟。
 */
export function resolveNotificationActivation(hasWindow: boolean): NotificationActivation {
  return hasWindow ? 'focus' : 'reopen'
}

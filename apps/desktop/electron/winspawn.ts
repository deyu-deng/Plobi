/**
 * Windows 控制台窗口抑制的**唯一来源**。
 *
 * 为什么要收口成一个模块（0.3.3 自更新黑窗事故的结论）：
 *
 * 1. Electron 主进程是 GUI 子系统，自己没有控制台。它 spawn 的每一个
 *    **控制台子系统**子进程（cmd.exe / powershell.exe / curl.exe / git.exe）
 *    在找不到可继承的控制台时，Windows 会给它分配一个新的——用户眼前就多
 *    一块黑窗。
 *
 * 2. `windowsHide: true` 只让 libuv 给**直接子进程**加
 *    `STARTF_USESHOWWINDOW = SW_HIDE`，对孙进程无效。cmd.exe 里面再起
 *    powershell.exe 时那条标志早就没了，所以只写 windowsHide 的调用点
 *    会「父进程不闪、孙进程闪」——黑窗里的内容正是安装脚本的输出。
 *
 * 3. `detached: true` 在 Windows 上等于 `DETACHED_PROCESS`：子进程**没有**
 *    控制台。于是它再起的 powershell 只能自己分配一个全新的、**可见的**
 *    控制台窗口。当 CREATE_NO_WINDOW 与 DETACHED_PROCESS 同时给出时，
 *    Windows 让 DETACHED_PROCESS 优先、CREATE_NO_WINDOW 被忽略，黑窗
 *    反而必现。
 *
 * 结论：Windows 上要「看不见 + 活得过父进程退出」，用 CREATE_NO_WINDOW
 * （子进程拥有一个**无窗口**控制台，孙进程继承同一个无窗口控制台），
 * 不要用 detached；跑不跑得下去由 `stdio: 'ignore'` + `child.unref()` 负责，
 * 与「有没有窗口」无关。
 */

export const CREATE_NO_WINDOW = 0x08000000
export const DETACHED_PROCESS = 0x00000008

/** Windows: 叠加 CREATE_NO_WINDOW；其它平台原样返回（creationflags 不适用）。 */
export function hiddenFlags(extra = 0): number {
  return process.platform === 'win32' ? extra | CREATE_NO_WINDOW : extra
}

/**
 * 「无窗口、且要活过本进程」的启动选项。
 *
 * 调用方负责再补 `stdio` 与 `child.unref()`。显式写明 `detached: false`：
 * 这条注释连带代码一起，防止下一个人凭直觉把 detached 加回来。
 */
export function windowlessLaunchOptions(): {
  detached: boolean
  creationflags?: number
  windowsHide: boolean
} {
  if (process.platform === 'win32') {
    return { detached: false, creationflags: hiddenFlags(), windowsHide: true }
  }

  return { detached: true, windowsHide: true }
}

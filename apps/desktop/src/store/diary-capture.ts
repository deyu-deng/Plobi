import { atom } from 'nanostores'

// 路过记一句的浮层开关。快捷键走 keybinds（`diary.capture`），所以焦点在输入框里时
// 本来就不会触发——编辑器开着的时候不需要它。
export const $diaryCaptureOpen = atom(false)

export function openDiaryCapture(): void {
  $diaryCaptureOpen.set(true)
}

export function closeDiaryCapture(): void {
  $diaryCaptureOpen.set(false)
}

export function toggleDiaryCapture(): void {
  $diaryCaptureOpen.set(!$diaryCaptureOpen.get())
}

/**
 * 「现在」那一根线：accent 条 ＋ 前头那颗点（裁定 40）。
 *
 * **两处用它**：右栏那根日程轴（`timeline.tsx`）与全屏日历那一张
 * （`app/agenda/calendar/calendar-view.tsx`）。同一个组件 ⇒ 两处的形状与走位
 * 永远一致，不会一边修好一边又走样。
 *
 * - **点与线同心**：点与线是同一个 flex 行的两端，由 `items-center` 一次居中；
 *   整行只搬一次（`-translate-y-1/2` 搬的是行自己）。点若自带位移，在已经
 *   居中的行里就会比线高半格——用户 11-07 截图里那颗点就是这么错开的。
 * - **落在「那一刻」上**：`topPx` 由调用方按「分钟 → 像素」那套映射算好，
 *   行的中心正落在那个像素上（行高 = 点高，`-translate-y-1/2` 搬的是整行）。
 * - **小数像素**：位置带小数，所以这条线是**连续爬**的，而不是每分钟跳一格。
 */

/**
 * 此刻是当天第几分钟——**带小数**（秒也算进去）。
 *
 * 只算到分钟的话，落点就被量化成 0.73px 的台阶；而更早的写法只在重渲染时
 * 读一次钟，然后那条线就不再动了。
 */
export function nowMinutesOfDay(date: Date): number {
  return (
    date.getHours() * 60 +
    date.getMinutes() +
    date.getSeconds() / 60 +
    date.getMilliseconds() / 60000
  )
}

export function NowLine({ testId, topPx }: { testId?: string; topPx: number }) {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 z-20 flex -translate-y-1/2 items-center"
      data-testid={testId}
      style={{ top: Math.round(topPx * 100) / 100 }}
    >
      <span className="size-1.5 shrink-0 rounded-full bg-(--ui-accent) shadow-[0_0_0.375rem_color-mix(in_srgb,var(--ui-accent)_55%,transparent)]" />
      <span className="block h-[2px] flex-1 bg-(--ui-accent)" />
    </div>
  )
}

/**
 * 采集频率那一格：**一颗旋钮 + 一个数字与单位的小框**（用户 10-27：
 * 「cron 我说了不用设计这么多挡位。你就做一个旋钮……**往一遍旋就是频率越来越高。反之就是越来越慢**。
 * 然后旁边放一个框。可以给一个输入框。可以调整单位和具体数字」）。
 *
 * 为什么不是四颗按钮：上一版是 `每轮 / 每 N 小时 / 每天 / 每周` 四个模式加一堆子控件——
 * 那是把 cron 的四种形状摊给他认，而不是"我要多勤"这一件事。旋钮只回答一个问题：**多勤**，
 * 顺 = 越来越勤，逆 = 越来越疏；旁边那两格负责"到底多勤"的精确值。
 *
 * 存的还是原来那一句（`plobi/collectors/cadence.py` 认的写法）：`every 90m`／`every 6h`／`every 2d`，
 * 最左边那一格是空串＝跟着全局每一轮。**这里不新增落库格式**，运行时一个字没动。
 */

import { useId, useRef } from 'react'

/** 这一格要的那几张话——**由调用方给**（`settings.sources` 那一片），这里不自己去抓语言目录。 */
export interface CadenceDialLabels {
  cadenceDial: string
  cadenceDialHint: string
  cadenceEveryN: string
  cadenceModeEveryRound: string
  cadenceNumber: string
  cadenceUnit: string
  cadenceUnits: { days: string, hours: string, minutes: string }
}

/**
 * 旋钮能停的位置，**从最慢到最快**（顺时针=越来越勤，与下面那句提示同一方向，用户 10-27 拍的就是这个方向）。
 * 最左那一格是空串＝跟着全局每一轮；两端都是真的边界：5 分钟以内由全局那一轮管，两周以上等于不采。
 */
const SLOW_TO_FAST: (number | '')[] = ['', 20160, 10080, 4320, 2880, 1440, 720, 480, 360, 240, 180, 120, 60, 30, 15, 10, 5]

const SWEEP = 250
const START = 145

/** 同一个间隔的整齐写法：`1440` 存成 `every 1d`，不存成 `every 1440m`（配置文件是给人看的）。 */
export function cadenceText(minutes: number): string {
  const { n, suffix } = unitOf(minutes)

  return `every ${n}${suffix}`
}

function unitOf(minutes: number): { label: keyof CadenceDialLabels['cadenceUnits'], n: number, suffix: string } {
  if (minutes % 1440 === 0) {
    return { label: 'days', n: minutes / 1440, suffix: 'd' }
  }

  if (minutes % 60 === 0) {
    return { label: 'hours', n: minutes / 60, suffix: 'h' }
  }

  return { label: 'minutes', n: minutes, suffix: 'm' }
}

/** 存的那一句 → 旋钮停在哪一档。认不出来的写法（每周固定几点那种 cron）不当"知道"，落回最左的"每一轮"。 */
function positionOf(value: string): number {
  const every = /^every\s+(\d+)\s*([mhd])$/i.exec(value.trim())
  const cron = /^(\d+)\s+(?:\*\/(\d+)|(\d+))\s+\*\s+\*\s+\*$/u.exec(value.trim())
  let minutes: null | number = null

  if (every) {
    const n = Number(every[1])
    const unit = every[2].toLowerCase()

    minutes = unit === 'd' ? n * 1440 : unit === 'h' ? n * 60 : n
  } else if (cron && cron[2]) {
    minutes = Number(cron[2]) * 60
  } else if (cron && cron[3]) {
    minutes = 1440
  }

  if (minutes === null) {
    return 0
  }

  let best = 0
  let gap = Number.POSITIVE_INFINITY

  SLOW_TO_FAST.forEach((step, i) => {
    if (step === '') {
      return
    }

    if (Math.abs(step - minutes) < gap) {
      gap = Math.abs(step - minutes)
      best = i
    }
  })

  return best
}

export function CadenceDial({ labels, onChange, value }: {
  labels: CadenceDialLabels
  onChange: (value: string) => void
  value: string
}) {
  const dial = useRef<null | HTMLElement>(null)
  const id = useId()
  const position = positionOf(value)
  const minutes = SLOW_TO_FAST[position] || 0

  const choose = (next: number) => {
    const landed = SLOW_TO_FAST[Math.min(SLOW_TO_FAST.length - 1, Math.max(0, Math.round(next)))]

    onChange(landed === '' ? '' : cadenceText(landed))
  }

  const angleFor = (at: number) => START + (at / (SLOW_TO_FAST.length - 1)) * SWEEP

  const fromPointer = (event: PointerEvent | React.PointerEvent) => {
    const node = dial.current

    if (!node) {
      return
    }

    const box = node.getBoundingClientRect()
    const dx = event.clientX - (box.left + box.width / 2)
    const dy = event.clientY - (box.top + box.height / 2)
    let angle = (Math.atan2(dy, dx) * 180) / Math.PI

    angle = ((angle % 360) + 360) % 360
    const offset = ((angle - START) % 360 + 360) % 360
    choose(offset <= SWEEP ? (offset / SWEEP) * (SLOW_TO_FAST.length - 1) : (offset - SWEEP > (360 - SWEEP) / 2 ? SLOW_TO_FAST.length - 1 : 0))
  }

  const onPointerDown = (event: React.PointerEvent) => {
    event.preventDefault()
    fromPointer(event)

    const move = (next: PointerEvent) => fromPointer(next)

    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
    }

    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    const keys: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }
    const step = keys[event.key]

    if (!step) {
      return
    }

    event.preventDefault()
    choose(position + step)
  }

  const degrees = angleFor(position) - 180
  const shown = unitOf(minutes || 1440)

  const sentence = minutes === 0
    ? labels.cadenceModeEveryRound
    : labels.cadenceEveryN.replace('{n}', String(shown.n)).replace('{unit}', labels.cadenceUnits[shown.label])

  return (
    <div className="flex flex-wrap items-center gap-3" data-testid="cadence-dial" data-value={value || 'per_round'}>
      <div
        aria-label={labels.cadenceDial}
        aria-valuemax={SLOW_TO_FAST.length - 1}
        aria-valuemin={0}
        aria-valuenow={position}
        aria-valuetext={sentence}
        className="relative size-[92px] shrink-0 cursor-grab touch-none outline-none focus-visible:ring-2 focus-visible:ring-(--ui-border-focus)"
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        ref={node => {
          dial.current = node
        }}
        role="slider"
        tabIndex={0}
      >
        <svg className="size-full" viewBox="0 0 100 100">
          <circle
            className="fill-(--ui-bg-secondary) stroke-(--ui-border)"
            cx="50"
            cy="50"
            fill="none"
            r="30"
            strokeWidth="1"
          />
          <g transform={`rotate(${degrees} 50 50)`}>
            <rect
              className="fill-(--ui-text-primary)"
              height="16"
              rx="2"
              width="4"
              x="48"
              y="12"
            />
          </g>
        </svg>
      </div>

      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-1.5">
          <label className="sr-only" htmlFor={`${id}-n`}>
            {labels.cadenceNumber}
          </label>
          <input
            aria-label={labels.cadenceNumber}
            className="w-16 rounded-md border border-(--ui-border) bg-transparent px-1.5 py-0.5 text-[length:var(--conversation-caption-font-size)]"
            id={`${id}-n`}
            min={1}
            onChange={event => {
              const n = Math.max(1, Math.round(Number(event.target.value) || 1))

              onChange(`every ${n}${shown.suffix}`)
            }}
            type="number"
            value={shown.n}
          />
          <label className="sr-only" htmlFor={`${id}-unit`}>
            {labels.cadenceUnit}
          </label>
          <select
            aria-label={labels.cadenceUnit}
            className="rounded-md border border-(--ui-border) bg-transparent px-1 py-0.5 text-[length:var(--conversation-caption-font-size)]"
            id={`${id}-unit`}
            onChange={event => {
              onChange(`every ${shown.n}${event.target.value}`)
            }}
            value={shown.suffix}
          >
            <option value="m">
              {labels.cadenceUnits.minutes}
            </option>
            <option value="h">
              {labels.cadenceUnits.hours}
            </option>
            <option value="d">
              {labels.cadenceUnits.days}
            </option>
          </select>
        </div>
        <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-secondary)">
          {sentence}
        </p>
        <p className="text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          {labels.cadenceDialHint}
        </p>
      </div>
    </div>
  )
}

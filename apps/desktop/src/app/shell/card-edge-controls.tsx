import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip } from '@/components/ui/tooltip'

/** 一枚边缘按钮（收起/展开面板、只显示这张卡）。 */
export interface CardEdgeControl {
  /** 不给就用侧栏那个默认图标。 */
  icon?: string
  label: string
  onClick: () => void
}

/**
 * 靠窗口边那张大卡右上角的两枚符号（用户 10-31）：
 * 收起/展开面板 + 只显示这一张卡。两张卡共用这一个渲染，
 * 免得一处是一套样式、另一处又是另一套。
 */
export interface CardEdgeControls {
  collapse?: CardEdgeControl
  maximize?: CardEdgeControl
}

const EDGE_BUTTON = 'grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground'

export function CardEdgeControlButtons({ controls }: { controls: CardEdgeControls }) {
  if (!controls.collapse && !controls.maximize) {
    return null
  }

  return (
    <>
      {controls.collapse ? (
        <Tip label={controls.collapse.label}>
          <Button
            aria-label={controls.collapse.label}
            className={EDGE_BUTTON}
            onClick={controls.collapse.onClick}
            size="icon-xs"
            type="button"
            variant="ghost"
          >
            <Codicon name={controls.collapse.icon ?? 'layout-sidebar-right'} size="0.85rem" />
          </Button>
        </Tip>
      ) : null}
      {controls.maximize ? (
        <Tip label={controls.maximize.label}>
          <Button
            aria-label={controls.maximize.label}
            className={EDGE_BUTTON}
            onClick={controls.maximize.onClick}
            size="icon-xs"
            type="button"
            variant="ghost"
          >
            <Codicon name="screen-full" size="0.85rem" />
          </Button>
        </Tip>
      ) : null}
    </>
  )
}

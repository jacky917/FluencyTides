import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  type HTMLAttributes,
  type ReactNode,
} from 'react'
import { ArrowDownToLine, ArrowUpToLine } from 'lucide-react'
import { useElasticOverscroll } from '../hooks/useElasticOverscroll'
import type { ScrollGesture } from '../lib/ankiFrame'
import { DEFAULT_MAX_PULL } from '../lib/elasticOverscroll'
import { cn } from '../lib/utils'

/** 預設的邊界提示。Default edge hints. */
const DEFAULT_HINTS = { top: '已經到頂了', bottom: '已經到底了' }

/** 拉到上限的多少比例時提示完全顯示。Fraction of the limit at which a hint is fully shown. */
const HINT_FULL_AT = 0.4

/** {@link ElasticScrollArea} 對外的操作。The imperative handle. */
export interface ElasticScrollHandle {
  /** 捲動容器本身（例如捲回頂端）。The scroll container. */
  readonly element: HTMLDivElement | null
  /** 處理 iframe 轉送過來的捲動手勢。Handles a gesture forwarded from an iframe. */
  handleGesture: (gesture: ScrollGesture) => void
}

/** {@link ElasticScrollArea} 的 props。Props of {@link ElasticScrollArea}. */
export interface ElasticScrollAreaProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** 是否啟用彈性回饋（例如系統「減少動態效果」時關掉）。Whether the effect is on. */
  enabled: boolean
  /** 位移上限（px，上下都是）。Displacement limit, both ways. */
  maxPull?: number
  /** 拉動時在空出來的位置顯示的提示；設為 null 不顯示。Edge hints; null hides one. */
  hints?: { top?: ReactNode | null; bottom?: ReactNode | null }
  /** 隱藏捲軸。Hides the scrollbar. */
  hideScrollbar?: boolean
  /** 放在捲動區最上方、不跟著位移的內容（例如固定的標題列）。Content above that does not move. */
  header?: ReactNode
  /** 會跟著位移的內容外框的 class。Class of the moving content. */
  contentClassName?: string
  children?: ReactNode
}

/**
 * 有 iOS 式彈性回饋的捲動區：捲到頂或底之後繼續拉（滾輪、觸控），內容跟著拉動但越拉越緊、
 * 不會超過 `maxPull`，空出來的位置淡入「已經到頂了／到底了」的提示，放開後彈回。
 * 捲動區本身要能捲動（由 `className` 給高度與 `overflow`）。內容中若有 iframe，iframe 內的手勢
 * 不會傳到這裡，要由 iframe 轉送後呼叫 `ref.handleGesture`。
 *
 * A scroll area with iOS-style rubber-band feedback and edge hints. Give it
 * a height and overflow via `className`; forward iframe gestures to
 * `ref.handleGesture`.
 */
export const ElasticScrollArea = forwardRef<ElasticScrollHandle, ElasticScrollAreaProps>(function ElasticScrollArea(
  {
    enabled,
    maxPull = DEFAULT_MAX_PULL,
    hints = DEFAULT_HINTS,
    hideScrollbar = false,
    header,
    className,
    contentClassName,
    children,
    ...rest
  },
  ref,
) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const topHintRef = useRef<HTMLDivElement>(null)
  const bottomHintRef = useRef<HTMLDivElement>(null)

  // 提示的透明度隨拉動距離變化；直接改 DOM，拉動時不必重新渲染
  const onPull = useCallback(
    (displacement: number, animated: boolean) => {
      const strength = Math.min(1, Math.abs(displacement) / (maxPull * HINT_FULL_AT))
      const targets: [HTMLDivElement | null, boolean][] = [
        [topHintRef.current, displacement > 0],
        [bottomHintRef.current, displacement < 0],
      ]
      for (const [hint, active] of targets) {
        if (!hint) continue
        hint.style.transition = animated ? 'opacity 300ms ease-out' : 'none'
        hint.style.opacity = active ? strength.toFixed(3) : '0'
      }
    },
    [maxPull],
  )
  const handleGesture = useElasticOverscroll(scrollerRef, contentRef, { enabled, maxPull, onPull })
  useImperativeHandle(
    ref,
    () => ({
      get element() {
        return scrollerRef.current
      },
      handleGesture,
    }),
    [handleGesture],
  )

  return (
    <div
      ref={scrollerRef}
      // 關掉瀏覽器自己的回彈（Safari、Android 會有），只留這裡的效果，才不會兩種疊在一起
      className={cn(
        enabled && 'overscroll-none',
        hideScrollbar && '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
        className,
      )}
      {...rest}
    >
      {header}
      <div ref={contentRef} className={cn('relative', contentClassName)}>
        {enabled && hints.top && (
          <EdgeHint ref={topHintRef} edge="top">
            {hints.top}
          </EdgeHint>
        )}
        {children}
      </div>
      {/* 底部提示不能放在內容裡：絕對定位、位移過的元素也會撐大可捲動的高度（多出一段空白）。
          放在零高度、貼住可視範圍底部的錨點上，往上拉時出現在空出來的位置 */}
      {enabled && hints.bottom && (
        <div className="pointer-events-none sticky bottom-0 h-0">
          <EdgeHint ref={bottomHintRef} edge="bottom">
            {hints.bottom}
          </EdgeHint>
        </div>
      )}
    </div>
  )
})

/**
 * 邊界提示，內容被拉開時出現在空出來的位置：頂部的貼在內容上緣外（往上超出不會增加可捲動高度），
 * 底部的貼在可視範圍底部的錨點上、往上延伸。
 * An edge hint: the top one sits just above the content, the bottom one
 * rises from an anchor stuck to the bottom of the viewport.
 */
const EdgeHint = forwardRef<HTMLDivElement, { edge: 'top' | 'bottom'; children: ReactNode }>(function EdgeHint(
  { edge, children },
  ref,
) {
  const Icon = edge === 'top' ? ArrowUpToLine : ArrowDownToLine
  return (
    <div
      ref={ref}
      aria-hidden="true"
      className={cn(
        'pointer-events-none absolute inset-x-0 flex items-center justify-center gap-1.5 py-4 text-xs text-muted-foreground opacity-0',
        edge === 'top' ? 'top-0 -translate-y-full' : 'bottom-0',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </div>
  )
})

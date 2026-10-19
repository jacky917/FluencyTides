/**
 * 偏好設定頁面：網頁顯示方面的個人設定，存在這個瀏覽器（localStorage），不影響後端。
 * 目前有卡片翻面動畫；後端的工具與模型設定在「Tool Settings」。
 *
 * Preferences: per-browser display settings (localStorage). Backend tool
 * and model settings live on the Tool Settings page.
 */

import { useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { FaceTransition } from '../components/FaceTransition'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { useFlipPreference } from '../hooks/useFlipPreference'
import {
  DEFAULT_FLIP_PREFERENCE,
  FACE_SPEEDS,
  FACE_TRANSITIONS,
  type FaceTransitionOption,
  type FaceTransitionSpeed,
  type FlipPreference,
} from '../lib/faceTransitions'
import { cn } from '../lib/utils'

/**
 * 偏好設定頁面。Preferences page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function Preferences() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">偏好設定</h2>
        <p className="text-muted-foreground mt-2">網頁顯示方面的設定，只存在這個瀏覽器。</p>
      </div>
      <FlipAnimationSettings />
    </div>
  )
}

/**
 * 卡片翻面動畫的設定：動畫種類（每種附可點的預覽）與速度。
 * Flip animation settings: the animation (each with a live preview) and the speed.
 * @returns 設定區塊 JSX。Section JSX.
 */
function FlipAnimationSettings() {
  const { preference, reducedMotion, setPreference } = useFlipPreference()
  const isDefault =
    preference.animation === DEFAULT_FLIP_PREFERENCE.animation && preference.speed === DEFAULT_FLIP_PREFERENCE.speed

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-lg">卡片翻面動畫</CardTitle>
          <CardDescription>卡片瀏覽頁切換正面與背面時播放。點預覽區可以試看。</CardDescription>
        </div>
        <Button variant="ghost" size="sm" disabled={isDefault} onClick={() => setPreference(DEFAULT_FLIP_PREFERENCE)}>
          <RotateCcw className="h-4 w-4" />
          恢復預設
        </Button>
      </CardHeader>
      <CardContent className="space-y-6">
        {reducedMotion && (
          <p role="status" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            系統開啟了「減少動態效果」，卡片瀏覽頁不會播放動畫；這裡的設定在關閉該選項後生效。
          </p>
        )}

        {/* 原生 radio：方向鍵切換、螢幕閱讀器讀得出選項；預覽鈕放在 label 外面 */}
        <fieldset className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <legend className="sr-only">翻面動畫</legend>
          {FACE_TRANSITIONS.map((option) => (
            <AnimationOption
              key={option.id}
              option={option}
              speed={preference.speed}
              selected={preference.animation === option.id}
              onSelect={() => setPreference({ animation: option.id })}
            />
          ))}
        </fieldset>

        <SpeedPicker value={preference.speed} onChange={(speed) => setPreference({ speed })} />
      </CardContent>
    </Card>
  )
}

/**
 * 一種動畫的選項卡：名稱、說明與可點的預覽。
 * One animation option: its name, description and a clickable preview.
 * @returns 選項 JSX。Option JSX.
 */
function AnimationOption({
  option,
  speed,
  selected,
  onSelect,
}: {
  option: FaceTransitionOption
  speed: FaceTransitionSpeed
  selected: boolean
  onSelect: () => void
}) {
  const [side, setSide] = useState<'front' | 'back'>('front')
  const preview: FlipPreference = { animation: option.id, speed }

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-lg border p-3 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
        selected ? 'border-primary ring-1 ring-primary' : 'hover:border-foreground/30',
      )}
    >
      <button
        type="button"
        onClick={() => setSide((prev) => (prev === 'front' ? 'back' : 'front'))}
        aria-label={`預覽「${option.label}」`}
        className="rounded-md bg-muted/60 p-3"
      >
        <FaceTransition value={side} resetKey={option.id} direction={side === 'back' ? 1 : -1} preference={preview}>
          {(shown) => <PreviewFace side={shown} />}
        </FaceTransition>
      </button>
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="radio"
          name="flip-animation"
          value={option.id}
          checked={selected}
          onChange={onSelect}
          className="sr-only"
        />
        <span
          aria-hidden="true"
          className={cn(
            'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border',
            selected ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/50',
          )}
        >
          {selected && <Check className="size-3" />}
        </span>
        <span>
          <span className="block text-sm font-medium">{option.label}</span>
          <span className="block text-xs text-muted-foreground">{option.description}</span>
        </span>
      </label>
    </div>
  )
}

/**
 * 預覽用的迷你卡面（深色，與 Anki 卡面相近）。A miniature card face for previews.
 * @returns 卡面 JSX。Face JSX.
 */
function PreviewFace({ side }: { side: 'front' | 'back' }) {
  return (
    <div className="flex h-20 flex-col items-center justify-center rounded-md bg-[#1e1e1e] text-white shadow-sm">
      <span className="text-[10px] uppercase tracking-widest text-white/50">{side === 'front' ? '正面' : '背面'}</span>
      <span className="text-lg font-semibold">{side === 'front' ? '絡まる' : 'からまる・纏住'}</span>
    </div>
  )
}

/**
 * 動畫速度的分段選擇。The speed segmented control.
 * @returns 選擇器 JSX。Picker JSX.
 */
function SpeedPicker({ value, onChange }: { value: FaceTransitionSpeed; onChange: (speed: FaceTransitionSpeed) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-sm font-medium">速度</span>
      <fieldset className="inline-flex rounded-md border p-0.5">
        <legend className="sr-only">動畫速度</legend>
        {(Object.entries(FACE_SPEEDS) as [FaceTransitionSpeed, { label: string; ms: number }][]).map(([id, speed]) => (
          <label
            key={id}
            className={cn(
              'cursor-pointer rounded px-3 py-1 text-sm has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
              value === id ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
            )}
          >
            <input
              type="radio"
              name="flip-speed"
              value={id}
              checked={value === id}
              onChange={() => onChange(id)}
              className="sr-only"
            />
            {speed.label}
            <span className="ml-1 text-xs opacity-70">{speed.ms}ms</span>
          </label>
        ))}
      </fieldset>
    </div>
  )
}

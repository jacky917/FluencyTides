/**
 * 偏好設定頁面：網頁顯示方面的個人設定，存在這個瀏覽器（localStorage），不影響後端。
 * 包含卡片動畫（翻面與換卡）；後端的工具與模型設定在「Tool Settings」。
 *
 * Preferences: per-browser display settings (localStorage). Backend tool
 * and model settings live on the Tool Settings page.
 */

import { useState } from 'react'
import { Check, RotateCcw } from 'lucide-react'
import { FaceTransition } from '../components/FaceTransition'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { useFaceSettings } from '../hooks/useFaceSettings'
import {
  DEFAULT_FACE_SETTINGS,
  FACE_SPEEDS,
  FACE_TRANSITIONS,
  type FaceOccasion,
  type FaceTransitionOption,
  type FaceTransitionSpeed,
  type FacePreference,
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
      <CardAnimationSettings />
    </div>
  )
}

/** 兩種情境的標題與說明。Titles of the two occasions. */
const OCCASIONS: readonly { id: FaceOccasion; label: string; description: string }[] = [
  { id: 'flip', label: '翻面', description: '同一張卡片切換正面與背面時' },
  { id: 'switch', label: '換卡', description: '換到另一張卡片時（選卡、從卡面跳到其他筆記、返回上一張）' },
]

/**
 * 卡片動畫的設定：翻面與換卡各選一種動畫（每種附可點的預覽），速度共用。
 * Card animation settings: one animation per occasion (each with a live preview), a shared speed.
 * @returns 設定區塊 JSX。Section JSX.
 */
function CardAnimationSettings() {
  const { settings, reducedMotion, setSettings } = useFaceSettings()
  const isDefault =
    settings.flip === DEFAULT_FACE_SETTINGS.flip &&
    settings.switch === DEFAULT_FACE_SETTINGS.switch &&
    settings.speed === DEFAULT_FACE_SETTINGS.speed

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-lg">卡片動畫</CardTitle>
          <CardDescription>卡片瀏覽頁翻面與換卡時播放，可以分別選擇。點預覽區可以試看。</CardDescription>
        </div>
        <Button variant="ghost" size="sm" disabled={isDefault} onClick={() => setSettings(DEFAULT_FACE_SETTINGS)}>
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

        {OCCASIONS.map((occasion) => (
          // 原生 radio：方向鍵切換、螢幕閱讀器讀得出選項；預覽鈕放在 label 外面
          <fieldset key={occasion.id} className="space-y-3">
            <legend className="mb-3">
              <span className="block text-sm font-medium">{occasion.label}</span>
              <span className="block text-xs text-muted-foreground">{occasion.description}</span>
            </legend>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {FACE_TRANSITIONS.map((option) => (
                <AnimationOption
                  key={option.id}
                  occasion={occasion.id}
                  option={option}
                  speed={settings.speed}
                  selected={settings[occasion.id] === option.id}
                  onSelect={() => setSettings({ [occasion.id]: option.id })}
                />
              ))}
            </div>
          </fieldset>
        ))}

        <SpeedPicker value={settings.speed} onChange={(speed) => setSettings({ speed })} />
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
  occasion,
  option,
  speed,
  selected,
  onSelect,
}: {
  occasion: FaceOccasion
  option: FaceTransitionOption
  speed: FaceTransitionSpeed
  selected: boolean
  onSelect: () => void
}) {
  // 翻面的預覽在同一張卡片的正反面之間切換，換卡的預覽在兩張卡片的正面之間切換
  const [first, second]: readonly [PreviewFaceId, PreviewFaceId] =
    occasion === 'flip' ? ['front', 'back'] : ['front', 'next']
  const [face, setFace] = useState<PreviewFaceId>(first)
  const preview: FacePreference = { animation: option.id, speed }

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-lg border p-3 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring',
        selected ? 'border-primary ring-1 ring-primary' : 'hover:border-foreground/30',
      )}
    >
      <button
        type="button"
        onClick={() => setFace((prev) => (prev === first ? second : first))}
        aria-label={`預覽${occasion === 'flip' ? '翻面' : '換卡'}的「${option.label}」`}
        // 翻頁會轉出卡面範圍，預覽區裁切在自己的框內
        className="overflow-hidden rounded-md bg-muted/60 p-3"
      >
        <FaceTransition
          value={face}
          resetKey={option.id}
          direction={face === second ? 1 : -1}
          preference={preview}
          faceClassName="rounded-md"
        >
          {(shown) => <PreviewFace face={shown} />}
        </FaceTransition>
      </button>
      <label className="flex cursor-pointer items-start gap-2">
        <input
          type="radio"
          name={`${occasion}-animation`}
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

/** 預覽用的迷你卡面：一張卡片的正反面，以及換卡時的另一張。The preview faces. */
type PreviewFaceId = 'front' | 'back' | 'next'

const PREVIEW_FACES: Readonly<Record<PreviewFaceId, { label: string; word: string; height: string }>> = {
  front: { label: '正面', word: '絡まる', height: 'h-16' },
  back: { label: '背面', word: 'からまる・纏住', height: 'h-24' },
  next: { label: '正面', word: '解ける', height: 'h-20' },
}

/**
 * 預覽用的迷你卡面（深色，與 Anki 卡面相近）。各面高度不同，看得出高度的過渡。
 * A miniature card face for previews; heights differ, showing the height tween.
 * @returns 卡面 JSX。Face JSX.
 */
function PreviewFace({ face }: { face: PreviewFaceId }) {
  const { label, word, height } = PREVIEW_FACES[face]
  return (
    <div
      className={cn('flex flex-col items-center justify-center rounded-md bg-[#1e1e1e] text-white shadow-sm', height)}
    >
      <span className="text-[10px] uppercase tracking-widest text-white/50">{label}</span>
      <span className="text-lg font-semibold">{word}</span>
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
          </label>
        ))}
      </fieldset>
    </div>
  )
}

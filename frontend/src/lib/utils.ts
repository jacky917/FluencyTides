import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/**
 * 合併 Tailwind class 名稱並解決衝突。
 * Merges Tailwind class names and resolves conflicts.
 * @param inputs - 任意 class 值。Arbitrary class values.
 * @returns 合併後的 class 字串。The merged class string.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

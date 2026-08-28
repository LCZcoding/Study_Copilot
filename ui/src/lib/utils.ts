import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/**
 * cn() — shadcn/ui 标准工具：合并 className + 解决 Tailwind 冲突。
 * 例：cn("px-2 py-1", condition && "bg-primary", "px-4") -> "py-1 bg-primary px-4"
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

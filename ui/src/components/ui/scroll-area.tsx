import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * 简易 ScrollArea —— 不装 @radix-ui/react-scroll-area。
 * 用 overflow-y-auto + 自定义滚动条样式（已在 index.css）实现。
 */
export const ScrollArea = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("overflow-y-auto overflow-x-hidden", className)}
    {...props}
  >
    {children}
  </div>
))
ScrollArea.displayName = "ScrollArea"

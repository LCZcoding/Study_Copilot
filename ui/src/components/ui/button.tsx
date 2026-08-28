import * as React from "react"
import { cn } from "@/lib/utils"

// shadcn/ui 风格的 Button —— 手工实现，避免 npm 装 @shadcn/ui。
// 用 cva（class-variance-authority）做 variant 管理。

const buttonVariants = {
  default:
    "bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm",
  ghost: "hover:bg-muted hover:text-foreground",
  outline:
    "border border-border bg-transparent hover:bg-muted hover:text-foreground",
  secondary:
    "bg-secondary text-secondary-foreground hover:bg-secondary/80",
  destructive:
    "bg-destructive text-destructive-foreground hover:bg-destructive/90",
}

const sizeVariants = {
  default: "h-9 px-4 py-2 text-sm",
  sm: "h-8 px-3 text-xs",
  lg: "h-11 px-6 text-base",
  icon: "h-9 w-9",
}

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof buttonVariants
  size?: keyof typeof sizeVariants
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          // 基础
          "inline-flex items-center justify-center gap-2 rounded-md font-medium",
          "transition-all duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "disabled:pointer-events-none disabled:opacity-50",
          // variant + size
          buttonVariants[variant],
          sizeVariants[size],
          className,
        )}
        {...props}
      />
    )
  },
)
Button.displayName = "Button"

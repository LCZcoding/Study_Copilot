import { cn } from "@/lib/utils"

/**
 * LoadingDots —— 三个小点依次跳动的打字指示器。
 * 不用 spinner —— spinner 太"机械"，三个点更像"AI 在思考"。
 * animation-delay 让第二个点比第一个慢 0.16s，第三个慢 0.32s。
 */
export function LoadingDots({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      <span
        className="h-1.5 w-1.5 rounded-full bg-primary/80 animate-bounce-dot"
        style={{ animationDelay: "0ms" }}
      />
      <span
        className="h-1.5 w-1.5 rounded-full bg-primary/60 animate-bounce-dot"
        style={{ animationDelay: "160ms" }}
      />
      <span
        className="h-1.5 w-1.5 rounded-full bg-primary/40 animate-bounce-dot"
        style={{ animationDelay: "320ms" }}
      />
      <span className="ml-2 text-xs text-muted-foreground italic">
        Thinking...
      </span>
    </div>
  )
}

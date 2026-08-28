import { useState, type KeyboardEvent } from "react"
import { Send, Square } from "lucide-react"
import { Button } from "./ui/button"
import { cn } from "@/lib/utils"

/**
 * InputBar —— 底部固定的 chat 输入栏。
 *
 * 设计要点：
 * - 64px 高度，圆角 + amber focus 边框
 * - Enter 发送 / Shift+Enter 换行
 * - 发送中显示 Stop 按钮（PR1 占位，PR2+ 接 SSE 流式时启用真 stop）
 * - placeholder 用 monospace + primary 色（"AI 风"）
 */
export function InputBar({
  onSend,
  disabled,
  isLoading,
}: {
  onSend: (text: string) => void
  disabled?: boolean
  isLoading?: boolean
}) {
  const [value, setValue] = useState("")

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled || isLoading) return
    onSend(trimmed)
    setValue("")
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t border-border/40 bg-background/60 backdrop-blur-md px-6 py-4">
      <div
        className={cn(
          "flex items-end gap-2 max-w-3xl mx-auto",
          "rounded-xl border border-border/60 bg-card/40 backdrop-blur-sm",
          "p-2 transition-all duration-200",
          "focus-within:border-primary/60 focus-within:shadow-[0_0_0_3px_hsl(32_50%_60%/0.08)]",
        )}
      >
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask anything..."
          disabled={disabled}
          rows={1}
          className={cn(
            "flex-1 resize-none bg-transparent",
            "px-3 py-2 text-sm text-foreground placeholder:font-mono placeholder:text-primary/40",
            "focus:outline-none",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "max-h-32",
          )}
          style={{ minHeight: "36px" }}
          onInput={(e) => {
            const t = e.currentTarget
            t.style.height = "auto"
            t.style.height = Math.min(t.scrollHeight, 128) + "px"
          }}
        />

        {isLoading ? (
          <Button
            variant="ghost"
            size="icon"
            disabled
            className="flex-shrink-0"
            aria-label="Stop generation"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            onClick={handleSend}
            disabled={!value.trim() || disabled}
            size="icon"
            className="flex-shrink-0"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>

      {/* 提示 */}
      <p className="mt-2 text-center text-[10px] text-muted-foreground/50 font-mono">
        Enter to send · Shift+Enter for new line
      </p>
    </div>
  )
}

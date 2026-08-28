import { Bot, User, FileText } from "lucide-react"
import { cn } from "@/lib/utils"
import { renderMarkdown } from "@/lib/markdown"
import { LoadingDots } from "./LoadingDots"
import type { ChatMessage } from "@/lib/types"

/**
 * MessageBubble —— 单条消息（user / assistant）。
 *
 * 设计要点：
 * - User 消息：amber 左边框 + 左对齐，靠下（人在问）
 * - Assistant 消息：墨绿卡片背景 + 砖红 left border + markdown 渲染
 * - Sources chips：hover 显示完整路径
 * - Error 状态：红边
 * - Pending 状态：LoadingDots
 */
export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user"
  const isError = !!message.error

  const html = !isUser && !isError ? renderMarkdown(message.content) : null

  return (
    <div
      className={cn(
        "flex gap-3 animate-message-in",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      {/* 左侧 avatar（assistant 显示） */}
      {!isUser && (
        <div
          className={cn(
            "flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center",
            isError
              ? "bg-destructive/20 text-destructive"
              : "bg-secondary/30 text-secondary-foreground",
          )}
        >
          <Bot className="h-4 w-4" />
        </div>
      )}

      <div className={cn("flex flex-col gap-2 max-w-[85%]", isUser && "items-end")}>
        {/* 气泡主体 */}
        <div
          className={cn(
            "rounded-lg px-4 py-3",
            isUser && "border-l-4 border-primary bg-primary/5",
            !isUser && !isError && "border-l-4 border-accent bg-card/70 backdrop-blur-sm",
            isError && "border-l-4 border-destructive bg-destructive/5",
          )}
        >
          {message.pending ? (
            <LoadingDots />
          ) : isError ? (
            <div className="text-sm text-destructive">
              <span className="font-semibold">⚠ Error: </span>
              {message.error}
            </div>
          ) : isUser ? (
            <p className="text-sm text-foreground whitespace-pre-wrap break-words">
              {message.content}
            </p>
          ) : (
            <div
              className="prose-library text-sm"
              dangerouslySetInnerHTML={{ __html: html ?? "" }}
            />
          )}
        </div>

        {/* Sources chips（仅 assistant 且有 sources） */}
        {!isUser && message.sources && message.sources.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pl-1">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70 font-mono">
              cited:
            </span>
            {message.sources.map((src) => (
              <span
                key={src}
                title={src}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md",
                  "border border-border/60 bg-muted/40",
                  "px-2 py-0.5 text-[11px] text-muted-foreground",
                  "hover:border-primary/50 hover:text-foreground transition-colors",
                  "cursor-default",
                )}
              >
                <FileText className="h-2.5 w-2.5" />
                <span className="max-w-[200px] truncate">{src}</span>
              </span>
            ))}
          </div>
        )}

        {/* 时间戳 */}
        <span className="text-[10px] text-muted-foreground/50 font-mono pl-1">
          {new Date(message.createdAt).toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      </div>

      {/* 右侧 avatar（user 显示） */}
      {isUser && (
        <div className="flex-shrink-0 h-8 w-8 rounded-full bg-primary/15 text-primary flex items-center justify-center">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}

import { useEffect, useRef } from "react"
import { ScrollArea } from "./ui/scroll-area"
import { InputBar } from "./InputBar"
import { MessageBubble } from "./MessageBubble"
import { EmptyState } from "./EmptyState"
import type { ChatMessage } from "@/lib/types"

/**
 * ChatPanel —— 中间主聊天区。
 *
 * 设计要点：
 * - 顶部留白（不是 ChatGPT 那种满屏）
 * - 自动滚到底部（新消息进来时）
 * - 空状态显示手绘"台灯 + 书本"插画
 * - 消息入场动画（animate-message-in）
 */
export function ChatPanel({
  messages,
  isLoading,
  onSend,
}: {
  messages: ChatMessage[]
  isLoading: boolean
  onSend: (text: string) => void
}) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // 新消息时自动滚到底部
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    // 用 requestAnimationFrame 等 DOM 更新完再滚
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" })
    })
  }, [messages, isLoading])

  return (
    <main className="flex-1 flex flex-col min-w-0 h-full">
      {/* 消息区 */}
      <ScrollArea ref={scrollRef} className="flex-1 px-6 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 ? (
            <div className="flex items-center justify-center min-h-[60vh]">
              <EmptyState />
            </div>
          ) : (
            messages.map((m) => <MessageBubble key={m.id} message={m} />)
          )}
        </div>
      </ScrollArea>

      {/* 输入栏（底部固定） */}
      <InputBar onSend={onSend} isLoading={isLoading} disabled={isLoading} />
    </main>
  )
}

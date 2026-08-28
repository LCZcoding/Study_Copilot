import { useCallback, useState } from "react"
import { api, APIError } from "@/lib/api"
import type { ChatMessage } from "@/lib/types"

/**
 * useChat —— ChatPanel 的状态管理 hook。
 *
 * PR1 范围：
 * - 状态：messages 数组 + isLoading
 * - 行为：send() 调 /api/chat，加 user + assistant 两条 message
 * - 错误：捕获 APIError，标到 assistant 消息上
 *
 * PR2+ 加：
 * - 流式响应（SSE / fetch + ReadableStream）
 * - abort controller（stop 生成）
 * - localStorage 持久化
 */
export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)

  const send = useCallback(async (text: string) => {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    }

    const pendingAssistant: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      createdAt: new Date().toISOString(),
      pending: true,
    }

    setMessages((prev) => [...prev, userMsg, pendingAssistant])
    setIsLoading(true)

    try {
      const res = await api.chat({ question: text, top_k: 3, min_score: 0.3 })

      const assistantMsg: ChatMessage = {
        id: pendingAssistant.id,
        role: "assistant",
        content: res.answer,
        sources: res.sources,
        createdAt: new Date().toISOString(),
      }

      setMessages((prev) =>
        prev.map((m) => (m.id === pendingAssistant.id ? assistantMsg : m)),
      )
    } catch (err) {
      const errorMsg: ChatMessage = {
        id: pendingAssistant.id,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        error:
          err instanceof APIError
            ? `${err.status} ${err.message}`
            : err instanceof Error
              ? err.message
              : "Unknown error",
      }

      setMessages((prev) =>
        prev.map((m) => (m.id === pendingAssistant.id ? errorMsg : m)),
      )
    } finally {
      setIsLoading(false)
    }
  }, [])

  const clear = useCallback(() => {
    setMessages([])
  }, [])

  return { messages, isLoading, send, clear }
}

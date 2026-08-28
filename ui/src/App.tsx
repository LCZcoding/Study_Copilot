import { useEffect, useState } from "react"
import { Toaster, toast } from "sonner"
import { Sidebar } from "@/components/Sidebar"
import { ChatPanel } from "@/components/ChatPanel"
import { useChat } from "@/hooks/useChat"
import { api, APIError } from "@/lib/api"

/**
 * App 根组件 —— 整体布局。
 *
 * 布局：左 Sidebar（文档导航） + 右 ChatPanel（聊天）
 * 顶部状态：拉 /api/health + /api/documents 显示文档数（PR1 接真数据）
 *
 * 错误处理：API 拉失败时显示 toast（用 sonner）
 */
export default function App() {
  const { messages, isLoading, send } = useChat()
  const [docCount, setDocCount] = useState(0)
  const [totalChunks, setTotalChunks] = useState(0)

  // 启动时拉文档列表 + 健康检查
  useEffect(() => {
    void (async () => {
      try {
        const [health, docs] = await Promise.all([
          api.health(),
          fetch("http://localhost:8000/api/documents").then((r) => r.json()),
        ])
        setDocCount(docs.documents?.length ?? 0)
        setTotalChunks(docs.total_chunks ?? 0)

        if (health.status !== "ok") {
          toast.warning("RAG 服务状态异常", {
            description: `status=${health.status}`,
          })
        }
      } catch (err) {
        const msg =
          err instanceof APIError
            ? `${err.status} ${err.message}`
            : err instanceof Error
              ? err.message
              : "无法连接 RAG 服务"
        toast.error("RAG 服务不可达", {
          description: `${msg}（请确认后端在 localhost:8000 运行）`,
          duration: Infinity,
        })
      }
    })()
  }, [])

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar docCount={docCount} totalChunks={totalChunks} />
      <ChatPanel messages={messages} isLoading={isLoading} onSend={send} />
      <Toaster
        position="bottom-right"
        theme="dark"
        richColors
        closeButton
        toastOptions={{
          style: {
            background: "hsl(24 16% 12%)",
            border: "1px solid hsl(28 14% 22%)",
            color: "hsl(35 33% 84%)",
          },
        }}
      />
    </div>
  )
}

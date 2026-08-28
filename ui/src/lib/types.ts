/**
 * 与后端 FastAPI Pydantic 模型对齐的 TypeScript 类型。
 * 修改后端时记得同步这里（v0.6+ 接 OpenAPI 自动生成）。
 */

export interface ChatRequest {
  question: string
  top_k?: number
  min_score?: number
}

export interface ChatResponse {
  answer: string
  sources: string[]
}

export interface HealthResponse {
  status: string
  indexed_chunks: number
  components: Record<string, string>
}

export interface DocumentInfo {
  source_key: string
  source_type: string
  source_name: string
  chunks: number
}

export interface DocumentsResponse {
  documents: DocumentInfo[]
  total_chunks: number
}

export type ChatRole = "user" | "assistant" | "system"

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  sources?: string[]
  /** ISO timestamp */
  createdAt: string
  /** 是否正在流式输出（PR2 接 SSE 时用） */
  pending?: boolean
  /** 错误信息（如网络失败） */
  error?: string
}

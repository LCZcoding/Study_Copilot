import type { ChatRequest, ChatResponse, HealthResponse } from "./types"

/**
 * API base URL — 开发时指向 FastAPI (localhost:8000)。
 * 生产环境可通过 Vite 环境变量 VITE_API_BASE 覆盖。
 */
const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000"

export class APIError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = "APIError"
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  })

  if (!res.ok) {
    // Read body once (Response body is a stream — can't read twice).
    // Try JSON first, fall back to text, finally to statusText.
    const text = await res.text()
    let message = res.statusText || "Unknown error"
    try {
      const body = JSON.parse(text)
      message = body.detail || body.message || text || message
    } catch {
      // not JSON — use raw text if non-empty
      if (text) message = text
    }
    throw new APIError(res.status, message)
  }

  return res.json() as Promise<T>
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  chat: (req: ChatRequest, signal?: AbortSignal) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify(req),
      signal,
    }),
}

export { API_BASE }

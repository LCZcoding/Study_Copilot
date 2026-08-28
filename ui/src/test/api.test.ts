import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { api, APIError } from "@/lib/api"

describe("api.chat", () => {
  let originalFetch: typeof fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it("posts to /api/chat and returns parsed response", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: "hi", sources: ["a.md"] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    const res = await api.chat({ question: "test" })

    expect(res.answer).toBe("hi")
    expect(res.sources).toEqual(["a.md"])
    expect(mockFetch).toHaveBeenCalledTimes(1)

    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toContain("/api/chat")
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body)).toEqual({ question: "test" })
  })

  it("throws APIError with status and message on non-2xx", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    // Run once and inspect the rejected error (Response body is a stream,
    // can't be consumed twice).
    let err: unknown
    try {
      await api.chat({ question: "test" })
    } catch (e) {
      err = e
    }

    expect(err).toBeInstanceOf(APIError)
    expect((err as APIError).status).toBe(404)
    expect((err as APIError).message).toContain("Not found")
  })

  it("falls back to statusText when response body is not JSON", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response("plain text error", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    let err: unknown
    try {
      await api.chat({ question: "test" })
    } catch (e) {
      err = e
    }

    expect(err).toBeInstanceOf(APIError)
    expect((err as APIError).status).toBe(500)
    expect((err as APIError).message).toContain("plain text error")
  })

  it("passes AbortSignal to fetch for cancellation", async () => {
    const controller = new AbortController()
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: "x", sources: [] }), { status: 200 }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    await api.chat({ question: "test" }, controller.signal)

    const [, init] = mockFetch.mock.calls[0]
    expect(init.signal).toBe(controller.signal)
  })
})

describe("api.health", () => {
  it("returns health payload", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "ok",
          indexed_chunks: 100,
          components: { embedder: "ready", retriever: "ready", llm: "ready" },
        }),
        { status: 200 },
      ),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    const h = await api.health()
    expect(h.status).toBe("ok")
    expect(h.indexed_chunks).toBe(100)
  })
})

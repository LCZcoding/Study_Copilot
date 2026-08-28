import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChat } from "@/hooks/useChat"

describe("useChat", () => {
  let originalFetch: typeof fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it("starts with empty messages and not loading", () => {
    const { result } = renderHook(() => useChat())
    expect(result.current.messages).toEqual([])
    expect(result.current.isLoading).toBe(false)
  })

  it("appends user + assistant messages on successful send", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: "the answer", sources: ["x.md"] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send("hello?")
    })

    expect(result.current.messages).toHaveLength(2)
    const [userMsg, assistantMsg] = result.current.messages

    expect(userMsg.role).toBe("user")
    expect(userMsg.content).toBe("hello?")
    expect(userMsg.error).toBeUndefined()

    expect(assistantMsg.role).toBe("assistant")
    expect(assistantMsg.content).toBe("the answer")
    expect(assistantMsg.sources).toEqual(["x.md"])
    expect(assistantMsg.error).toBeUndefined()
    expect(assistantMsg.pending).toBeFalsy()

    expect(result.current.isLoading).toBe(false)
  })

  it("marks assistant message with error on failure", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "no docs" }), { status: 404 }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send("test")
    })

    const assistantMsg = result.current.messages[1]
    expect(assistantMsg.error).toContain("404")
    expect(assistantMsg.error).toContain("no docs")
    expect(assistantMsg.content).toBe("")
    expect(result.current.isLoading).toBe(false)
  })

  it("clears messages when clear() is called", async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ answer: "x", sources: [] }), { status: 200 }),
    )
    globalThis.fetch = mockFetch as unknown as typeof fetch

    const { result } = renderHook(() => useChat())

    await act(async () => {
      await result.current.send("a")
    })
    expect(result.current.messages).toHaveLength(2)

    act(() => {
      result.current.clear()
    })
    expect(result.current.messages).toEqual([])
  })
})

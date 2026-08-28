import { describe, it, expect } from "vitest"
import { cn } from "@/lib/utils"

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("px-2", "py-1")).toBe("px-2 py-1")
  })

  it("filters out falsy values", () => {
    expect(cn("px-2", false && "hidden", null, undefined, "py-1")).toBe("px-2 py-1")
  })

  it("resolves Tailwind conflicts (later wins)", () => {
    // tailwind-merge: px-2 then px-4 should yield only px-4
    expect(cn("px-2", "px-4")).toBe("px-4")
  })

  it("preserves non-conflicting utilities", () => {
    expect(cn("px-2", "py-1", "bg-primary")).toBe("px-2 py-1 bg-primary")
  })

  it("handles conditional classes", () => {
    const isActive = true
    const isDisabled = false
    expect(cn("base", isActive && "active", isDisabled && "disabled")).toBe("base active")
  })
})

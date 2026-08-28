import { describe, it, expect } from "vitest"
import { renderMarkdown } from "@/lib/markdown"

describe("renderMarkdown", () => {
  it("escapes HTML to prevent XSS", () => {
    const html = renderMarkdown('<script>alert("xss")</script>')
    expect(html).not.toContain("<script>")
    expect(html).toContain("&lt;script&gt;")
  })

  it("renders headings", () => {
    expect(renderMarkdown("# H1")).toContain("<h1>H1</h1>")
    expect(renderMarkdown("## H2")).toContain("<h2>H2</h2>")
    expect(renderMarkdown("### H3")).toContain("<h3>H3</h3>")
  })

  it("renders fenced code blocks with language", () => {
    const md = "```python\ndef hello():\n    print('hi')\n```"
    const html = renderMarkdown(md)
    expect(html).toContain("<pre>")
    expect(html).toContain('class="language-python"')
    expect(html).toContain("def hello():")
    // content inside <code> should retain its body (escaped for safety)
    expect(html).toContain("print")
  })

  it("renders inline code", () => {
    const html = renderMarkdown("Use `npm install` to install.")
    expect(html).toContain("<code>npm install</code>")
  })

  it("renders bold and italic", () => {
    const htmlBold = renderMarkdown("**important**")
    expect(htmlBold).toContain("<strong>important</strong>")

    const htmlItalic = renderMarkdown("*emphasis*")
    expect(htmlItalic).toContain("<em>emphasis</em>")
  })

  it("renders unordered lists", () => {
    const md = "- apple\n- banana\n- cherry"
    const html = renderMarkdown(md)
    expect(html).toContain("<ul>")
    expect(html).toContain("<li>apple</li>")
    expect(html).toContain("<li>banana</li>")
    expect(html).toContain("<li>cherry</li>")
  })

  it("renders ordered lists", () => {
    const md = "1. first\n2. second\n3. third"
    const html = renderMarkdown(md)
    expect(html).toContain("<ol>")
    expect(html).toContain("<li>first</li>")
  })

  it("renders blockquotes", () => {
    const html = renderMarkdown("> quoted text")
    expect(html).toContain("<blockquote>quoted text</blockquote>")
  })

  it("renders links with target=_blank", () => {
    const html = renderMarkdown("[docs](https://example.com)")
    expect(html).toContain('href="https://example.com"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it("handles empty input", () => {
    // Whitespace-only input may produce an empty <p> wrapper; the important
    // invariant is that the output is empty after trimming HTML tags.
    expect(renderMarkdown("").replace(/<[^>]+>/g, "")).toBe("")
    expect(renderMarkdown("   \n\n  ").replace(/<[^>]+>/g, "")).toBe("")
  })

  it("preserves code block content through paragraph splitting", () => {
    // Bug regression: <pre> blocks should not be wrapped in <p>
    const md = "intro\n\n```\ncode\n```\n\noutro"
    const html = renderMarkdown(md)
    expect(html).toContain("<pre>")
    // The <pre> should appear directly, not nested inside <p>
    expect(html).not.toContain("<p><pre>")
  })
})

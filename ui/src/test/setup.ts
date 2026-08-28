import "@testing-library/jest-dom/vitest"

// jsdom doesn't implement matchMedia or IntersectionObserver by default.
// Stub them so components that might use them don't crash.

if (typeof window !== "undefined") {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })
  }
}

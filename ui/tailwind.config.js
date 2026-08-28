import type { Config } from "tailwindcss"

/**
 * "Late Night Library" theme — Study Co-pilot 的视觉语言。
 *
 * 设计灵感：深夜台灯下的木桌 + 老书脊 + 琥珀色光晕。
 * 避开 generic AI 审美（Inter / 紫色渐变 / Space Grotesk）。
 * 用色：暖色 amber + 墨绿 + 砖红，背景深棕黑代替纯黑。
 */
const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // 显示字体：现代衬线，有书卷气（避开 Inter / Space Grotesk）
        display: ['"Fraunces"', "ui-serif", "Georgia", "serif"],
        // 正文字体：Geist，Vercel 出品，现代但独特
        sans: ['"Geist"', "ui-sans-serif", "system-ui", "sans-serif"],
        // 等宽：代码片段
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      keyframes: {
        // 打字指示器：三个小点依次跳动
        "bounce-dot": {
          "0%, 80%, 100%": { transform: "translateY(0)", opacity: "0.4" },
          "40%": { transform: "translateY(-6px)", opacity: "1" },
        },
        // 消息入场：下浮 + 渐显
        "message-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        // 空状态插画：轻微浮动（呼吸感）
        "breathe": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
        // 光晕扫过（hover）
        "shimmer": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "bounce-dot": "bounce-dot 1.4s ease-in-out infinite",
        "message-in": "message-in 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
        "breathe": "breathe 3s ease-in-out infinite",
        "shimmer": "shimmer 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
}

export default config

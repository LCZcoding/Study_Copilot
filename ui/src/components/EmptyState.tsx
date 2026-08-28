import { cn } from "@/lib/utils"

/**
 * EmptyState —— CSS 手绘"台灯 + 书本 + 咖啡杯"。
 * 不引外部图片/SVG 文件，纯 CSS 几何形状拼出来。
 * 整体动画：breathe（呼吸式轻微浮动）。
 *
 * 视觉：暖色调（amber 台灯光 + 深棕书桌），契合 Late Night Library 主题。
 */
export function EmptyState({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-6 animate-breathe",
        className,
      )}
    >
      {/* 插画区：180x140 */}
      <div className="relative h-[140px] w-[180px]">
        {/* 桌面（横线 + 微弱阴影） */}
        <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-border to-transparent" />

        {/* 书（叠两本） */}
        <div className="absolute bottom-2 left-6 w-20 h-3 rounded-sm bg-secondary/80 shadow-md" />
        <div className="absolute bottom-5 left-9 w-20 h-3 rounded-sm bg-secondary shadow-md" />
        {/* 书脊细节 */}
        <div className="absolute bottom-[10px] left-12 w-px h-2 bg-secondary-foreground/40" />
        <div className="absolute bottom-[18px] left-16 w-px h-2 bg-secondary-foreground/40" />

        {/* 咖啡杯 */}
        <div className="absolute bottom-2 right-8 w-5 h-6 rounded-b-lg border-2 border-accent/60 bg-accent/20">
          {/* 杯把手 */}
          <div className="absolute -right-1.5 top-1 w-2 h-3 rounded-r-full border-2 border-l-0 border-accent/60" />
          {/* 蒸汽（用 before/after 动画） */}
          <div className="absolute -top-3 left-1 w-px h-2 bg-muted-foreground/40 animate-breathe" />
          <div
            className="absolute -top-4 left-2.5 w-px h-2 bg-muted-foreground/30 animate-breathe"
            style={{ animationDelay: "0.5s" }}
          />
        </div>

        {/* 台灯底座 */}
        <div className="absolute bottom-2 left-1/2 -translate-x-1/2 w-6 h-1 rounded-full bg-muted-foreground/50" />
        {/* 台灯杆 */}
        <div
          className="absolute bottom-3 left-1/2 -translate-x-1/2 w-px h-12 bg-muted-foreground/60"
          style={{ transform: "translateX(-50%) rotate(8deg)", transformOrigin: "bottom" }}
        />
        {/* 灯罩（梯形 + amber 光晕） */}
        <div
          className="absolute top-0 left-1/2 -translate-x-1/2 w-0 h-0"
          style={{
            borderLeft: "14px solid transparent",
            borderRight: "14px solid transparent",
            borderTop: "16px solid hsl(32 50% 60%)",
            filter: "drop-shadow(0 0 12px hsl(32 50% 60% / 0.6))",
          }}
        />
      </div>

      {/* 文案 */}
      <div className="text-center space-y-2 max-w-md">
        <h2 className="font-display text-2xl text-foreground">
          深夜图书馆
        </h2>
        <p className="text-sm text-muted-foreground leading-relaxed">
          提个问题开始探索你的知识库。
          <br />
          <span className="font-mono text-xs text-primary/80">
            Ask anything. I'll cite my sources.
          </span>
        </p>
      </div>
    </div>
  )
}

import { Library, FileText } from "lucide-react"
import { cn } from "@/lib/utils"

/**
 * Sidebar —— 左侧文档导航。
 *
 * PR1 范围：静态占位 UI + "库里有 N 个文档"显示（动态从 /api/documents 拉），
 *         但实际功能（点开 / 搜索 / 同步飞书）放到 PR2。
 *
 * 设计要点：
 * - Late Night Library 主题：墨绿色书脊 + 木质感
 * - 顶部 Logo 区（Fraunces 字体 + Library icon）
 * - 文档列表卡片样式（hover 琥珀色边框）
 */
export function Sidebar({
  docCount,
  totalChunks,
}: {
  docCount: number
  totalChunks: number
}) {
  return (
    <aside
      className={cn(
        "flex flex-col h-full w-64 flex-shrink-0",
        "border-r border-border/40 bg-background/40 backdrop-blur-sm",
      )}
    >
      {/* Logo 区 */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-border/30">
        <div
          className={cn(
            "flex h-9 w-9 items-center justify-center rounded-md",
            "bg-primary/15 text-primary",
          )}
        >
          <Library className="h-5 w-5" />
        </div>
        <div className="flex flex-col">
          <h1 className="font-display text-base font-semibold text-foreground leading-tight">
            Study Co-pilot
          </h1>
          <span className="text-[10px] text-muted-foreground font-mono uppercase tracking-wider">
            Late Night Library
          </span>
        </div>
      </div>

      {/* 文档统计 */}
      <div className="px-5 py-4 border-b border-border/30">
        <div className="flex items-baseline gap-2">
          <span className="font-display text-2xl font-semibold text-primary">
            {docCount}
          </span>
          <span className="text-xs text-muted-foreground">documents</span>
        </div>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="font-mono text-sm text-secondary-foreground">
            {totalChunks}
          </span>
          <span className="text-[11px] text-muted-foreground">chunks indexed</span>
        </div>
      </div>

      {/* 文档列表区（PR1 占位） */}
      <div className="flex-1 overflow-y-auto px-3 py-4">
        <div className="px-2 mb-2 text-[10px] uppercase tracking-wider text-muted-foreground/70 font-mono">
          Indexed Sources
        </div>

        {docCount === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-muted-foreground/60">
            <FileText className="h-6 w-6 mx-auto mb-2 opacity-40" />
            No documents yet
            <div className="mt-1 text-[10px]">
              Upload via the API or sync from Lark
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            <PlaceholderDocList count={Math.min(docCount, 5)} />
            {docCount > 5 && (
              <div className="px-2 py-1 text-[10px] text-muted-foreground/50 font-mono">
                + {docCount - 5} more (PR2)
              </div>
            )}
          </div>
        )}
      </div>

      {/* 底部信息 */}
      <div className="px-5 py-3 border-t border-border/30">
        <div className="text-[10px] text-muted-foreground/60 font-mono space-y-0.5">
          <div className="flex justify-between">
            <span>API</span>
            <span className="text-secondary-foreground">localhost:8000</span>
          </div>
          <div className="flex justify-between">
            <span>Version</span>
            <span className="text-secondary-foreground">v0.5-3 PR1</span>
          </div>
        </div>
      </div>
    </aside>
  )
}

/**
 * 占位的文档卡片（PR1 不接真数据，用占位 UI 让用户看到布局效果）。
 * PR2 会替换为真实数据。
 */
function PlaceholderDocList({ count }: { count: number }) {
  const placeholders = [
    { name: "(进行中)算法__hot100.md", chunks: 162, icon: "📚" },
    { name: "命令__网络代理.md", chunks: 8, icon: "⚙️" },
    { name: "命令__端口占用.md", chunks: 3, icon: "⚙️" },
    { name: "命令__where.exe查找.md", chunks: 2, icon: "⚙️" },
  ].slice(0, count)

  return (
    <>
      {placeholders.map((doc) => (
        <button
          key={doc.name}
          className={cn(
            "group w-full text-left",
            "flex items-center gap-2 rounded-md px-2 py-1.5",
            "text-xs text-foreground/80",
            "hover:bg-muted/40 hover:border-primary/30",
            "border border-transparent transition-colors",
            "cursor-default", // PR2 改成 pointer
          )}
        >
          <span className="text-base leading-none">{doc.icon}</span>
          <div className="flex-1 min-w-0">
            <div className="truncate font-medium">{doc.name}</div>
            <div className="text-[10px] text-muted-foreground/60 font-mono">
              {doc.chunks} chunks
            </div>
          </div>
        </button>
      ))}
    </>
  )
}

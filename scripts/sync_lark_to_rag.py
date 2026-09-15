"""用 Lark CLI 把飞书 wiki 同步到 RAG 的命令行入口。

本文件只做两件事：
1. 进程级设置（Windows 终端 UTF-8，避免 ✓ ⚠ 等符号炸 GBK 控制台）
2. 解析命令行参数 → 构造 SyncConfig → 调 lark_sync.sync.run_sync()

业务逻辑全部在 lark_sync/ 包内，不要往这个文件里加流程代码。

背景：飞书自建应用读个人 wiki 受 JWT scope 限制，用
[Lark CLI](https://github.com/larksuite/cli) 在用户机器上做 OAuth，
脚本调 CLI 导出内容，再通过 /upload 入库。
"""
import argparse
import io
import sys
from pathlib import Path

# Windows 默认 stdout 是 GBK，打印 ✓ ⚠ ❌ 这些 Unicode 符号会爆。
# 重设 stdout 为 UTF-8（Python 3.7+ 支持 reconfigure）。
# 出错（极老的 Windows）就退化——打印替换符而不是崩。
# 必须在 import lark_sync 之前执行：包内模块 import 时没有输出，但保持
# "一切业务 import 之前先修好控制台"这个顺序最不容易踩坑。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    # Python < 3.7 或 sandboxed env —— 装个错误处理让 print 至少不崩
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 直接以脚本方式运行（python scripts/sync_lark_to_rag.py）时，
# Python 会把脚本所在目录 scripts/ 放进 sys.path[0]，因此同目录的
# lark_sync 包可以直接 import，无需改 PYTHONPATH / 安装成包。
from lark_sync.sync import SyncConfig, run_sync


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用 Lark CLI 同步飞书 wiki 到 RAG（v0.7：增量 + 删除传播）"
    )
    parser.add_argument(
        "--service-url",
        default="http://localhost:8000",
        help="RAG 服务地址（默认 http://localhost:8000）",
    )
    parser.add_argument(
        "--output-dir",
        default="./lark-exports",
        help="Lark CLI 导出文件保存目录（默认 ./lark-exports）",
    )
    parser.add_argument(
        "--lark-bin",
        default=None,
        help="lark-cli 可执行文件路径（也可用 $env:LARK_BIN；都不传则走 PATH）",
    )
    parser.add_argument(
        "--space",
        action="append",
        default=None,
        help="只同步指定 space_id（可多次传），不传则同步全部",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只导出 + 落盘，不上传/不删除（调试用，会列出 would-delete 清单）",
    )
    parser.add_argument(
        "--format",
        choices=["xml", "markdown", "im-markdown"],
        default="markdown",
        help="docs +fetch 输出格式（透传给 lark-cli，默认 markdown）",
    )
    parser.add_argument(
        "--scope",
        choices=["full", "outline", "range", "keyword", "section"],
        default="full",
        help="docs +fetch 读取范围（默认 full）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过「库里已有 chunks 警告」的 Enter 确认",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="包含私有 wiki（默认只同步企业公开 visibility=public 的 space）。"
             "user 身份登录后私有 wiki 也能读到内容，默认排除，避免个人文档误入知识库。",
    )
    parser.add_argument(
        "--no-delete-propagation",
        dest="delete_propagation",
        action="store_false",
        help="关闭删除传播：飞书侧已删除/改名的文档不从 RAG 库移除（默认会删）。"
             "删除不可逆，但仅对本次成功列出的 space 生效；被过滤掉或列出失败的 "
             "space 始终受保护，不会误删。",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    cfg = SyncConfig(
        service_url=args.service_url,
        output_dir=Path(args.output_dir).resolve(),
        lark_bin=args.lark_bin,
        spaces=args.space,
        dry_run=args.dry_run,
        doc_format=args.format,
        scope=args.scope,
        assume_yes=args.yes,
        include_private=args.include_private,
        delete_propagation=args.delete_propagation,
    )
    return run_sync(cfg)


if __name__ == "__main__":
    sys.exit(main())

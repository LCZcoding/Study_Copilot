"""v0.5-2 D ext: 用 Lark CLI 把飞书内容同步到 RAG。

背景：飞书自建应用读个人 wiki 受 JWT scope 限制（之前 OAuth 测过只拿 'auth:user.id:read'）。

解决方案：用 [Lark CLI](https://github.com/larksuite/cli) 在用户机器上做 OAuth，
我们脚本调 CLI 导出内容，再通过 /upload 入库。

v0.5-2 D ext 简化（按之前教训不做太多）：
- 不做定时调度（用户用 OS cron）
- 不做增量检测
- 不做 Lark CLI retry
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import httpx


# ========== Lark CLI 封装 ==========


class LarkCLIError(RuntimeError):
    """Lark CLI 进程成功但返回 {"ok": false, ...}。

    跟 subprocess 级 RuntimeError 区分，方便上层针对性提示用户
    （比如「请重新登录」「scope 不够」），而不是笼统报命令失败。
    """

    def __init__(self, message: str, payload: Optional[dict] = None):
        super().__init__(message)
        self.payload = payload  # 原始 {"ok": false, "error": ...}


class LarkCLI:
    """Lark CLI 的最小封装（v0.5-2 D ext 第二版）。

    真实命令（用户实测）：
        lark-cli auth status --format json
        lark-cli wiki +space-list --format json --page-all
        lark-cli wiki +node-list  --space-id <sid> --format json --page-all
        lark-cli docs  +fetch     --doc <obj_token> --doc-format markdown --scope full

    所有命令不接受位置参数，必须用 flag（--node-token / --doc / --space-id）。
    返回统一外壳 {"ok": bool, "data": {...}}，本封装已做 ok 校验，
    上层只看到 dict / 抛 LarkCLIError。

    参考：https://github.com/larksuite/cli/blob/main/README.md
    """

    def __init__(self, lark_bin: str = "lark-cli"):
        self.lark_bin = lark_bin
        if not shutil.which(lark_bin):
            raise RuntimeError(
                f"找不到 {lark_bin} 命令。请先安装 Lark CLI：\n"
                "  npx @larksuite/cli@latest install\n"
                "或：git clone https://github.com/larksuite/cli && make install\n"
                "详细文档：https://github.com/larksuite/cli"
            )

    # ---------- 底层 ----------

    def _run(self, args: List[str], timeout: int = 60) -> dict:
        """调一次 lark-cli，返已 parse 的 dict（已做 ok 校验）。

        抛出：
            RuntimeError：subprocess 失败 / 超时 / JSON 解析失败 / 缺 ok 字段
            LarkCLIError：进程成功但 {"ok": false}
        """
        cmd = [self.lark_bin] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"lark-cli 超时（>{timeout}s）：{' '.join(cmd)}\n"
                f"  提示：文档很大时拉长 --timeout 或用 --scope outline"
            ) from e

        if proc.returncode != 0:
            raise RuntimeError(
                f"lark-cli 命令失败（退出码 {proc.returncode}）：\n"
                f"  命令：{' '.join(cmd)}\n"
                f"  stderr：{(proc.stderr or '').strip()[:500]}"
            )

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"lark-cli 返回的不是合法 JSON：\n"
                f"  命令：{' '.join(cmd)}\n"
                f"  stdout 前 500 字符：{proc.stdout[:500]}\n"
                f"  错误：{e}"
            )

        if not isinstance(payload, dict) or "ok" not in payload:
            raise RuntimeError(
                f"lark-cli 返回缺少 ok 字段：\n"
                f"  命令：{' '.join(cmd)}\n"
                f"  stdout：{proc.stdout[:500]}"
            )

        if not payload.get("ok"):
            err = payload.get("error") or {}
            raise LarkCLIError(
                f"lark-cli 业务失败：{err}",
                payload=payload,
            )

        return payload.get("data") or {}

    # ---------- 业务方法 ----------

    def auth_status(self) -> dict:
        """lark-cli auth status --format json → data 字段。"""
        return self._run(["auth", "status", "--format", "json"], timeout=15)

    def list_wiki_spaces(self) -> List[dict]:
        """lark-cli wiki +space-list --format json --page-all。

        返回 [{name, space_id, open_sharing, visibility, ...}, ...]
        """
        data = self._run(
            ["wiki", "+space-list", "--format", "json", "--page-all"],
            timeout=30,
        )
        return data.get("spaces", []) or []

    def list_wiki_nodes(self, space_id: str) -> List[dict]:
        """lark-cli wiki +node-list --space-id <sid> --format json --page-all。

        返回 [{node_token, obj_token, obj_type, title, has_child, ...}, ...]
        """
        data = self._run(
            [
                "wiki", "+node-list",
                "--space-id", space_id,
                "--format", "json",
                "--page-all",
            ],
            timeout=60,
        )
        return data.get("nodes", []) or []

    def fetch_document_markdown(
        self,
        obj_token: str,
        doc_format: str = "markdown",
        scope: str = "full",
    ) -> str:
        """lark-cli docs +fetch --doc <obj_token> --doc-format markdown --scope full。

        返回 data.document.content（真正的 markdown 字符串）。

        抛出 LarkCLIError 当返回 content 为空（文档真的空 / 权限不足 / obj_type 不支持）。
        """
        data = self._run(
            [
                "docs", "+fetch",
                "--doc", obj_token,
                "--doc-format", doc_format,
                "--scope", scope,
            ],
            timeout=60,
        )
        doc = data.get("document") or {}
        content = doc.get("content") or ""
        if not content.strip():
            raise LarkCLIError(
                f"docs +fetch 返回空内容（obj_token={obj_token[:12]}...，"
                f"doc_format={doc_format}, scope={scope}）",
                payload=data,
            )
        return content


# ========== filename 工具 ==========

# Windows / Linux 都禁用的文件名字符。Windows 还禁 < > : " | ? *，加 NUL。
# 参考：https://learn.microsoft.com/windows/win32/fileio/naming-a-file
import re
import unicodedata

_INVALID_FNAME_CHARS = re.compile(r'[\\/:*?"<>|\x00]')
_MULTI_UNDERSCORE = re.compile(r"_+")
_MAX_TITLE_LEN = 80


def sanitize_filename(name: str) -> str:
    """把任意字符串清理成合法文件名片段。

    规则：
    - NFKC 归一化（处理全角字符，比如「／」→「/」→ '_'）
    - 去掉 / 替换非法字符 \\ / : * ? " < > | \\0 为 _
    - 合并连续下划线，去首尾下划线/空格/点
    - 截断到 80 字符
    - 全空返 'untitled'
    """
    if not name:
        return "untitled"
    name = unicodedata.normalize("NFKC", name).strip()
    name = _INVALID_FNAME_CHARS.sub("_", name)
    name = _MULTI_UNDERSCORE.sub("_", name).strip("_. ")
    if len(name) > _MAX_TITLE_LEN:
        name = name[:_MAX_TITLE_LEN].rstrip("_. ")
    return name or "untitled"


def build_export_filename(space_name: str, title: str, node_token: str) -> str:
    """导出文件的最终名：<space>__<title>__<token8>.md。

    末尾加 node_token 前 8 位，防止不同 space 出现同名文档时覆盖。
    """
    s = sanitize_filename(space_name)
    t = sanitize_filename(title)
    prefix = (node_token or "no000000")[:8]
    return f"{s}__{t}__{prefix}.md"


# ========== 同步流程 ==========

# 跟 app/data/loader.py:27 SUPPORTED_EXTENSIONS 对齐。
# 脚本只产 .md（飞书 markdown），但留 .markdown/.txt 兜底。
_SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
_MIME_MAP = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


def upload_to_rag(
    service_url: str,
    files: List[Path],
    timeout: int = 120,
    show_progress: bool = True,
) -> dict:
    """串行上传 md/txt 文件到 RAG /upload。

    返回：{"uploaded": int, "failed": [...], "skipped": [...], "total_chunks": int}
    """
    result = {"uploaded": 0, "failed": [], "skipped": [], "total_chunks": 0}
    total = len(files)

    for idx, file_path in enumerate(files, start=1):
        suffix = file_path.suffix.lower()

        if suffix not in _SUPPORTED_SUFFIXES:
            reason = f"后缀 {suffix} 不在 loader 白名单"
            result["skipped"].append({"file": file_path.name, "reason": reason})
            if show_progress:
                print(f"  ⊘ [{idx}/{total}] {file_path.name}（{reason}）")
            continue

        mime = _MIME_MAP[suffix]

        try:
            with httpx.Client(timeout=timeout) as client:
                with open(file_path, "rb") as fp:
                    resp = client.post(
                        f"{service_url}/api/upload",
                        files={"file": (file_path.name, fp, mime)},
                    )
        except httpx.TimeoutException:
            err = f"timeout {timeout}s"
            result["failed"].append({"file": file_path.name, "error": err})
            if show_progress:
                print(f"  ✗ [{idx}/{total}] {file_path.name}：{err}")
            continue
        except httpx.HTTPError as e:
            result["failed"].append({"file": file_path.name, "error": str(e)})
            if show_progress:
                print(f"  ✗ [{idx}/{total}] {file_path.name}：{e}")
            continue

        if resp.status_code == 200:
            body = resp.json()
            chunks = body.get("chunks", 0)
            result["uploaded"] += 1
            result["total_chunks"] += chunks
            if show_progress:
                print(f"  ✓ [{idx}/{total}] {file_path.name}：{chunks} chunks")
        else:
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            result["failed"].append({"file": file_path.name, "error": err})
            if show_progress:
                print(f"  ✗ [{idx}/{total}] {file_path.name}：{err[:100]}")

    return result


def _check_rag_service(service_url: str, timeout: int = 5) -> Optional[dict]:
    """调 /api/documents 看 RAG 服务现状。失败返 None（不打 error，让上层决定）。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{service_url}/api/documents")
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用 Lark CLI 同步飞书 wiki 到 RAG（v0.5-2 D ext 第二版）"
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
        default="lark-cli",
        help="lark-cli 可执行文件路径",
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
        help="只导出 + 落盘，不上传（调试用）",
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
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()

    # 0. dry-run 跳过 / 否则查 RAG 现状
    if not args.dry_run:
        existing = _check_rag_service(args.service_url)
        if existing is None:
            print(f"⚠️  无法连接 RAG 服务 {args.service_url}（服务没启动？）")
            print(f"   上传阶段会失败，但会先把 .md 落盘到 {output_dir}/")
        else:
            total_chunks = existing.get("total_chunks", 0)
            doc_count = len(existing.get("documents", []))
            if total_chunks > 0:
                print(f"⚠️  RAG 库里已有 {total_chunks} 个 chunks（{doc_count} 个文档）")
                print("   每次 POST /upload 会触发 fetch_documents() 全量 re-parse + re-embed。")
                print("   重复上传会让 chunks 翻倍（服务端没去重逻辑）。")
                if not args.yes:
                    print()
                    try:
                        input("按 Enter 继续，Ctrl+C 退出... ")
                    except EOFError:
                        print("⚠️  非交互环境，跳过确认（用 --yes 显式跳过这个 prompt）")

    # 1. 实例化 LarkCLI
    try:
        cli = LarkCLI(lark_bin=args.lark_bin)
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1
    print("✓ Lark CLI 已检测到")

    # 2. 认证检查（不强失败，warn 后继续）
    try:
        auth = cli.auth_status()
        identity = auth.get("identity", "unknown")
        print(f"✓ 已登录 Lark CLI（identity={identity}）")
    except (RuntimeError, LarkCLIError) as e:
        print(f"⚠️  查 auth 失败：{e}")
        print("   继续尝试列 wiki（可能 list 时也会失败）")

    # 3. 列 wiki spaces
    if args.space:
        spaces = [{"space_id": sid, "name": f"space_{sid[:8]}"} for sid in args.space]
        print(f"✓ 用 --space 指定 {len(spaces)} 个 space")
    else:
        try:
            spaces = cli.list_wiki_spaces()
        except LarkCLIError as e:
            print(f"❌ 列 wiki 失败（业务错）：{e}")
            print("   可能 scope 不够 → 重新跑 lark-cli auth login --recommend")
            return 1
        except RuntimeError as e:
            print(f"❌ 列 wiki 失败（命令错）：{e}")
            return 1

    print(f"✓ 找到 {len(spaces)} 个 wiki space")

    if not spaces:
        print("ℹ️  没有 wiki 可同步。可能原因：")
        print("   1. 个人 wiki 还没设为'企业公开'（飞书后台 → wiki 设置 → 权限）")
        print("   2. 没给 Lark CLI 正确 scope（重新跑 lark-cli auth login --recommend）")
        return 0

    # 4. 逐 space 列 nodes + fetch + 落盘
    output_dir.mkdir(parents=True, exist_ok=True)
    written_files: List[Path] = []
    by_type: dict = {}            # {obj_type: count}
    skipped_by_type: dict = {}    # {obj_type: [title, ...]}

    for space in spaces:
        sid = space.get("space_id") or space.get("id") or ""
        if not sid:
            print(f"  ⚠️  space 缺 space_id，跳过：{space}")
            continue
        sname = space.get("name") or sid[:8]

        print(f"\n--- Space: {sname} ({sid[:12]}...) ---")

        try:
            nodes = cli.list_wiki_nodes(sid)
        except LarkCLIError as e:
            print(f"  ✗ 列节点失败：{e}")
            continue
        except RuntimeError as e:
            print(f"  ✗ 列节点命令失败：{e}")
            continue

        print(f"  找到 {len(nodes)} 个节点")

        for node in nodes:
            obj_type = node.get("obj_type", "unknown")
            obj_token = node.get("obj_token") or ""
            title = node.get("title") or "(无标题)"
            node_token = node.get("node_token") or ""

            by_type[obj_type] = by_type.get(obj_type, 0) + 1

            # 本期只支持 docx
            if obj_type != "docx":
                skipped_by_type.setdefault(obj_type, []).append(title)
                print(f"    ⊘ {title}（obj_type={obj_type}，v0.5-2 只支持 docx）")
                continue

            if not obj_token:
                print(f"    ⚠️  {title} 缺少 obj_token，跳过")
                continue

            try:
                md = cli.fetch_document_markdown(
                    obj_token,
                    doc_format=args.format,
                    scope=args.scope,
                )
            except LarkCLIError as e:
                print(f"    ✗ {title} fetch 失败：{e}")
                continue
            except RuntimeError as e:
                print(f"    ✗ {title} fetch 异常：{e}")
                continue

            fname = build_export_filename(sname, title, node_token)
            out_path = output_dir / sname / fname
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md, encoding="utf-8")
            except OSError as e:
                print(f"    ✗ {title} 写盘失败：{e}")
                continue

            written_files.append(out_path)
            print(f"    ✓ {title} → {out_path.relative_to(output_dir)} ({len(md)} chars)")

    # 5. 汇总
    print(f"\n=== 落盘汇总 ===")
    print(f"  写入文件：{len(written_files)}")
    for t, c in sorted(by_type.items()):
        if t in skipped_by_type:
            print(f"  - obj_type={t}: {c}（跳过 {len(skipped_by_type[t])}）")
        else:
            print(f"  - obj_type={t}: {c}")

    if args.dry_run:
        print("  (dry-run 模式，跳过上传)")
        return 0

    if not written_files:
        print("  没有可上传的文件")
        return 0

    # 6. 上传
    print(f"\n--- 上传 {len(written_files)} 个文件到 {args.service_url} ---")
    result = upload_to_rag(args.service_url, written_files)

    print(f"\n=== 完成 ===")
    print(f"  成功：{result['uploaded']}")
    print(f"  失败：{len(result['failed'])}")
    if result["skipped"]:
        print(f"  跳过：{len(result['skipped'])}")
    print(f"  总 chunks：{result['total_chunks']}")
    if result["failed"]:
        print(f"\n  失败的文件：")
        for f in result["failed"]:
            print(f"    - {f['file']}：{f['error'][:100]}")

    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())

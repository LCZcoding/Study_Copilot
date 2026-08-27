"""v0.5-2 D ext: 用 Lark CLI 把飞书内容同步到 RAG。

背景：飞书 OAuth 自建应用读个人 wiki 受限（JWT scope 问题）。
用飞书官方 CLI 工具（Lark CLI）走另一条路：
1. Lark CLI 在用户机器上做 OAuth（绕过我们项目里的 OAuth scope 限制）
2. 我们脚本把 Lark CLI 导出的 markdown 文件，调 /upload 同步到 RAG

前置：
1. 用户安装 Lark CLI：npm install -g lark-cli
   或：npx skills add larksuite/cli -y -g
2. 第一次跑会弹浏览器让用户 OAuth（飞书自带流程）
3. 我们脚本调 lark-cli 把飞书内容导出到本地 ./lark-exports/
4. 我们脚本再调本地 /upload 入库

v0.5-2 D ext 简化：
- 不实现定时/后台运行（用户自己跑 + cron）
- 不实现增量检测（每次全量同步）
- Lark CLI 失败不重试（用户手动重跑）
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import httpx


# ========== Lark CLI 封装 ==========

class LarkCLI:
    """Lark CLI 的最小封装。

    v0.5-2 D ext 简化：只用了 list + export 两个子命令。
    用户装了 lark-cli 之后，应该能直接用。
    """

    def __init__(self, lark_bin: str = "lark-cli"):
        self.lark_bin = lark_bin
        # 验证装好了
        if not shutil.which(lark_bin):
            raise RuntimeError(
                f"找不到 {lark_bin} 命令。请先安装 Lark CLI：\n"
                "  npm install -g lark-cli\n"
                "或：npx skills add larksuite/cli -y -g\n"
                "详细文档：https://www.feishu.cn/content/article/7623291503305083853"
            )

    def _run(self, args: List[str], timeout: int = 60) -> str:
        """调一次 lark-cli，返 stdout。

        v0.5-2 D ext 简化：直接 subprocess.run，不做复杂的错误处理。
        用户能看到 stderr 输出来排错。
        """
        cmd = [self.lark_bin] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"lark-cli 命令失败（退出码 {result.returncode}）：\n"
                f"  命令：{' '.join(cmd)}\n"
                f"  stderr：{result.stderr[:500]}"
            )
        return result.stdout

    def list_wiki_spaces(self) -> List[dict]:
        """列出所有可访问的 wiki 空间。

        实际 Lark CLI 命令（用户装好后要 verify，可能是其中一个）：
        - lark-cli wiki list
        - lark-cli wiki spaces
        - lark-cli --type wiki list

        v0.5-2 D ext 简化：直接 subprocess，由 stderr 报错来定位。
        """
        output = self._run(["wiki", "list"])
        # 尝试解析 JSON；失败时打印原始输出
        try:
            data = json.loads(output)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            if isinstance(data, dict) and "spaces" in data:
                return data["spaces"]
            return [{"name": str(data)}]  # 兜底
        except json.JSONDecodeError:
            # 如果输出不是 JSON（如表格），提示用户调命令手动看
            raise RuntimeError(
                f"lark-cli 输出非 JSON 格式（你看下能不能手动解析）：\n{output[:1000]}"
            )

    def export_wiki(self, space_id: str, output_dir: Path) -> int:
        """导出整个 wiki 空间到本地目录。

        实际命令（要 verify）：
        - lark-cli wiki export <space_id> --output <dir>
        - lark-cli wiki download <space_id>

        返：导出文件数（粗略估算）
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        before = sum(1 for _ in output_dir.rglob("*") if _.is_file())

        # try 多种可能的命令
        commands_to_try = [
            ["wiki", "export", space_id, "--output", str(output_dir)],
            ["wiki", "download", space_id, "--output", str(output_dir)],
            ["wiki", "export", space_id, str(output_dir)],
        ]

        last_error = None
        for cmd_args in commands_to_try:
            try:
                self._run(cmd_args, timeout=120)
                break  # 成功就退出
            except RuntimeError as e:
                last_error = e
                continue
        else:
            raise RuntimeError(
                f"所有命令格式都失败。最后一个错误：\n{last_error}"
            )

        after = sum(1 for _ in output_dir.rglob("*") if _.is_file())
        return after - before


# ========== 同步流程 ==========

def upload_to_rag(
    service_url: str,
    files: List[Path],
    timeout: int = 60,
) -> dict:
    """把多个文件上传到我们的 /upload 端点。

    返：{"uploaded": N, "failed": [{filename, error}], "total_chunks": N}

    v0.5-2 D ext 简化：串行上传（一个一个来）。1 万文件可能要并行，留 v0.5-4。
    """
    result = {"uploaded": 0, "failed": [], "total_chunks": 0}

    for file_path in files:
        # 跳过非 markdown / 文本文件
        suffix = file_path.suffix.lower()
        if suffix not in {".md", ".markdown", ".txt", ".docx", ".pdf"}:
            print(f"  跳过 {file_path.name}（后缀 {suffix} 不支持）")
            continue

        # 推断 mime type
        mime = {
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".txt": "text/plain",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pdf": "application/pdf",
        }.get(suffix, "application/octet-stream")

        try:
            with httpx.Client(timeout=timeout) as client:
                with open(file_path, "rb") as fp:
                    resp = client.post(
                        f"{service_url}/api/upload",
                        files={"file": (file_path.name, fp, mime)},
                    )

            if resp.status_code == 200:
                body = resp.json()
                result["uploaded"] += 1
                result["total_chunks"] += body.get("chunks", 0)
                print(f"  ✓ {file_path.name} → {body.get('chunks', 0)} chunks")
            else:
                error_text = resp.text[:200]
                result["failed"].append({"file": file_path.name, "error": error_text})
                print(f"  ✗ {file_path.name}：HTTP {resp.status_code} - {error_text[:100]}")
        except Exception as e:
            result["failed"].append({"file": file_path.name, "error": str(e)})
            print(f"  ✗ {file_path.name}：{e}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="用 Lark CLI 同步飞书 wiki 到 RAG"
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
        help="lark-cli 可执行文件路径（默认 PATH 里能找到）",
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
        help="只导出，不上传（调试用）",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()

    # 1. 检查 lark-cli + 跑 OAuth 检查
    try:
        cli = LarkCLI(lark_bin=args.lark_bin)
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1

    print("✓ Lark CLI 已检测到")

    # 2. 列 wiki spaces
    if args.space:
        spaces = [{"space_id": sid, "name": f"space_{sid}"} for sid in args.space]
    else:
        try:
            spaces = cli.list_wiki_spaces()
        except RuntimeError as e:
            print(f"❌ 列出 wiki 失败：{e}")
            return 1

    print(f"✓ 找到 {len(spaces)} 个 wiki space")

    if not spaces:
        print("ℹ️  没有 wiki 可同步。你之前是否授权过 lark-cli？")
        print("   试试：lark-cli auth login（首次会弹浏览器）")
        return 0

    # 3. 逐个导出
    print(f"\n--- 导出到 {output_dir} ---")
    for space in spaces:
        sid = space.get("space_id") or space.get("id") or space.get("token")
        name = space.get("name") or sid
        print(f"\n  {name}（{sid[:12]}...）")

        try:
            count = cli.export_wiki(sid, output_dir / name)
            print(f"    → 导出 {count} 个文件")
        except RuntimeError as e:
            print(f"    ✗ 失败：{e}")
            continue

    # 4. 收集所有导出文件
    all_files = list(output_dir.rglob("*"))
    files_to_upload = [f for f in all_files if f.is_file()]

    # 过滤掉非支持格式
    supported_suffixes = {".md", ".markdown", ".txt", ".docx", ".pdf"}
    files_to_upload = [
        f for f in files_to_upload if f.suffix.lower() in supported_suffixes
    ]

    print(f"\n--- 找到 {len(files_to_upload)} 个可入库文件 ---")

    if args.dry_run:
        print(f"  (dry-run 模式，不上传)")
        for f in files_to_upload[:10]:
            print(f"    {f.relative_to(output_dir)}")
        if len(files_to_upload) > 10:
            print(f"    ...还有 {len(files_to_upload) - 10} 个")
        return 0

    # 5. 上传到 /upload
    print(f"\n--- 上传到 {args.service_url} ---")
    result = upload_to_rag(args.service_url, files_to_upload)

    # 6. 总结
    print(f"\n=== 完成 ===")
    print(f"  成功上传：{result['uploaded']}")
    print(f"  失败：{len(result['failed'])}")
    print(f"  总 chunks：{result['total_chunks']}")
    if result["failed"]:
        print(f"\n  失败的文件：")
        for f in result["failed"]:
            print(f"    - {f['file']}：{f['error'][:100]}")

    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())

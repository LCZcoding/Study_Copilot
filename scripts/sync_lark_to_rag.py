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

class LarkCLI:
    """Lark CLI 的最小封装（v0.5-2 D ext）。

    真实命令格式（从 GitHub README 拿到）：
    - 安装：npx @larksuite/cli@latest install
    - 配置：lark-cli config init
    - 登录：lark-cli auth login --recommend
    - wiki 命令：用 `+` 前缀的快捷命令（如 +list, +export），或完整路径
    - 输出格式：--format json（推荐，方便解析）

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

    def _run(self, args: List[str], timeout: int = 60) -> str:
        """调一次 lark-cli，返 stdout 文本。"""
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

    def auth_status(self) -> dict:
        """查当前登录状态。命令：lark-cli auth status --format json"""
        output = self._run(["auth", "status", "--format", "json"])
        return json.loads(output)

    def list_wiki_spaces(self) -> List[dict]:
        """列出所有可访问的 wiki 空间。

        v0.5-2 D ext：尝试多种命令格式（基于文档推断）
        - lark-cli wiki +list
        - lark-cli wiki spaces list
        - lark-cli wiki list

        实际命令用户装好后跑 `lark-cli wiki --help` 看真实命令。
        """
        commands_to_try = [
            ["wiki", "+list", "--format", "json", "--page-all"],
            ["wiki", "spaces", "list", "--format", "json", "--page-all"],
            ["wiki", "list", "--format", "json", "--page-all"],
        ]

        last_error = None
        for cmd_args in commands_to_try:
            try:
                output = self._run(cmd_args, timeout=30)
                data = json.loads(output)
                # 尝试多种返回结构
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    # 试常见字段
                    for key in ["items", "spaces", "data", "list"]:
                        if key in data and isinstance(data[key], list):
                            return data[key]
                # 单个 dict（不太可能但兜底）
                return [data]
            except (RuntimeError, json.JSONDecodeError) as e:
                last_error = e
                continue

        raise RuntimeError(
            f"所有 wiki list 命令格式都失败。错误：\n{last_error}\n"
            f"建议跑 `lark-cli wiki --help` 看真实命令"
        )

    def export_wiki(self, space_id: str, output_dir: Path) -> int:
        """导出整个 wiki 空间到本地目录。

        v0.5-2 D ext：尝试多种命令格式
        - lark-cli wiki +export <id> --output <dir>
        - lark-cli wiki nodes export --space-id <id> --output <dir>
        - lark-cli wiki +download <id> --output <dir>
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        before = sum(1 for _ in output_dir.rglob("*") if _.is_file())

        commands_to_try = [
            ["wiki", "+export", space_id, "--output", str(output_dir), "--format", "json"],
            ["wiki", "nodes", "export", "--params", json.dumps({"space_id": space_id}),
             "--output", str(output_dir)],
            ["wiki", "+download", space_id, "--output", str(output_dir)],
        ]

        last_error = None
        for cmd_args in commands_to_try:
            try:
                self._run(cmd_args, timeout=180)  # 导出可能慢
                break  # 成功就退出
            except RuntimeError as e:
                last_error = e
                continue
        else:
            raise RuntimeError(
                f"所有 wiki export 命令格式都失败。最后一个错误：\n{last_error}"
            )

        after = sum(1 for _ in output_dir.rglob("*") if _.is_file())
        return after - before


# ========== 同步流程 ==========

def upload_to_rag(
    service_url: str,
    files: List[Path],
    timeout: int = 60,
) -> dict:
    """把多个文件上传到我们的 /upload 端点。"""
    result = {"uploaded": 0, "failed": [], "total_chunks": 0}

    for file_path in files:
        # 跳过非支持格式
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
        help="只导出，不上传（调试用）",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()

    # 1. 检查 lark-cli
    try:
        cli = LarkCLI(lark_bin=args.lark_bin)
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1
    print("✓ Lark CLI 已检测到")

    # 2. 查认证状态（首次跑应该未登录）
    try:
        status = cli.auth_status()
        if not status.get("ok"):
            print("❌ 你还没登录 Lark CLI。请先跑：lark-cli auth login --recommend")
            return 1
        print(f"✓ 已登录 Lark CLI：{status.get('identity', 'unknown')}")
    except RuntimeError:
        print("⚠️  查 auth status 失败（可能命令格式不对）。继续尝试...")

    # 3. 列 wiki spaces
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
        print("ℹ️  没有 wiki 可同步。可能原因：")
        print("   1. 个人 wiki 还没设为'企业公开'（飞书后台 → wiki 设置 → 权限）")
        print("   2. 没给 Lark CLI 正确 scope（重新跑 lark-cli auth login --recommend）")
        return 0

    # 4. 逐个导出
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

    # 5. 收集所有可入库文件
    all_files = list(output_dir.rglob("*"))
    files_to_upload = [f for f in all_files if f.is_file()]

    supported_suffixes = {".md", ".markdown", ".txt", ".docx", ".pdf"}
    files_to_upload = [
        f for f in files_to_upload if f.suffix.lower() in supported_suffixes
    ]

    print(f"\n--- 找到 {len(files_to_upload)} 个可入库文件 ---")

    if args.dry_run:
        print("  (dry-run 模式，不上传)")
        for f in files_to_upload[:10]:
            print(f"    {f.relative_to(output_dir)}")
        if len(files_to_upload) > 10:
            print(f"    ...还有 {len(files_to_upload) - 10} 个")
        return 0

    # 6. 上传到 /upload
    print(f"\n--- 上传到 {args.service_url} ---")
    result = upload_to_rag(args.service_url, files_to_upload)

    # 7. 总结
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

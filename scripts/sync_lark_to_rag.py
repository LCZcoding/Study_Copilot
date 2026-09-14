"""v0.5-2 D ext: 用 Lark CLI 把飞书内容同步到 RAG。

背景：飞书自建应用读个人 wiki 受 JWT scope 限制（之前 OAuth 测过只拿 'auth:user.id:read'）。

解决方案：用 [Lark CLI](https://github.com/larksuite/cli) 在用户机器上做 OAuth，
我们脚本调 CLI 导出内容，再通过 /upload 入库。

v0.5-2 D ext 简化（按之前教训不做太多）：
- 不做定时调度（用户用 OS cron）
- 不做增量检测
- 不做 Lark CLI retry
"""
import hashlib
from datetime import datetime, timezone
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import httpx

# Windows 默认 stdout 是 GBK，打印 ✓ ⚠ ❌ 这些 Unicode 符号会爆。
# 重设 stdout 为 UTF-8（Python 3.7+ 支持 reconfigure）。
# 出错（极老的 Windows）就退化——打印替换符而不是崩。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    # Python < 3.7 或 sandboxed env —— 装个错误处理让 print 至少不崩
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


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

    def __init__(self, lark_bin: Optional[str] = None):
        """lark_bin 解析优先级：显式参数 > LARK_BIN 环境变量 > shutil.which("lark-cli")。

        记录来源（self._bin_source）让 _assert_bin_exists 能区分：
        - 显式 / 环境变量 → 用户**已经**指定了路径，subprocess.run 找不到时
          FileNotFoundError 更准（用户知道路径错了，看异常就知道）
        - PATH 查找 → shutil.which 找不到时给清晰安装提示（PATH 不全的可能）
        """
        # 1. 显式参数
        if lark_bin:
            self.lark_bin = lark_bin
            self._bin_source = "explicit"
            return
        # 2. 环境变量
        env_bin = os.environ.get("LARK_BIN")
        if env_bin:
            self.lark_bin = env_bin
            self._bin_source = "env"
            return
        # 3. PATH 查找
        which_result = shutil.which("lark-cli")
        if which_result:
            self.lark_bin = which_result
            self._bin_source = "which"  # shutil.which 找到了，subprocess 一般也能找到
        else:
            self.lark_bin = "lark-cli"  # 占位，subprocess 会 FileNotFoundError
            self._bin_source = "missing"  # 提示用户装 / 调 PATH

    def _assert_bin_exists(self) -> None:
        """只在 PATH 来源（_bin_source=missing）时检查；其他来源留给 subprocess 自己报。"""
        if self._bin_source != "missing":
            return
        # 这时 shutil.which("lark-cli") 返 None + self.lark_bin 还是字面 "lark-cli"
        raise RuntimeError(
            "找不到 lark-cli 命令。三种排查方向：\n"
            "  1. 装：npx @larksuite/cli@latest install（或 npm install -g @larksuite/cli）\n"
            "  2. 确认 PATH 包含 npm 全局目录（PowerShell 跑 `where.exe lark-cli` 验证）\n"
            "  3. 显式传 --lark-bin <full path>（推荐，跨 shell 都稳）：\n"
            "     uv run python scripts/sync_lark_to_rag.py --lark-bin \"$(npm config get prefix)/lark-cli.cmd\"\n"
            "  4. 或设环境变量：$env:LARK_BIN = \"<full path>\""
        )

    # ---------- 底层 ----------

    def _run(self, args: List[str], timeout: int = 60) -> dict:
        """调一次 lark-cli，返已 parse 的 dict（已做 ok 校验）。

        抛出：
            RuntimeError：命令不存在 / subprocess 失败 / 超时 / 找不到 JSON 响应
            LarkCLIError：进程返回 {"ok": false}（无论 exit code 是多少）

        实现注意：
        - Windows + 中文输出 → 不能用 text=True（默认 GBK 会爆）
        - 用 bytes + encoding='utf-8' 显式解码（lark-cli Node 写，统一 UTF-8）
        - lark-cli 把 JSON 业务错打到 stderr 或 stdout 不固定；两边都看
        - 退出码非 0 不一定是错——业务 ok:false 也可能用非零码（比如 126）
        """
        self._assert_bin_exists()
        # 显式 --as user：同步脚本读的是"用户的" wiki/文档，bot 身份看不到
        # 个人资源，且 bot 查用户资源会返回空成功而非报错（之前 0 个 space 的坑）。
        # 显式指定后身份不再靠 CLI 自动选（defaultAs=auto 不可控），
        # user token 过期时会明确抛 LarkCLIError 而不是静默空列表。
        cmd = [self.lark_bin] + args + ["--as", "user"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=False,           # 拿 bytes 自己解码
                timeout=timeout,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"subprocess 找不到 {self.lark_bin!r}：{e}\n"
                f"  PowerShell 的 where.exe 找得到 ≠ Python subprocess 找得到（PATH 差异）\n"
                f"  修法：--lark-bin \"<full path>\"  或  $env:LARK_BIN = \"<full path>\""
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"lark-cli 超时（>{timeout}s）：{' '.join(cmd)}\n"
                f"  提示：文档很大时拉长 --timeout 或用 --scope outline"
            ) from e

        # lark-cli 把 JSON 打到 stdout 或 stderr 不固定；都试试
        # 用 utf-8 严格模式解码，errors='replace' 避免单字符坏编码导致整个 stdout=None
        raw_out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        raw_err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

        # 优先尝试解析 stdout；空再试 stderr
        payload = None
        for source in (raw_out, raw_err):
            if not source.strip():
                continue
            # 跳过非 JSON 内容（比如进度条 / 装饰输出）
            try:
                payload = json.loads(source)
                break
            except json.JSONDecodeError:
                continue

        if payload is None:
            # 完全没解析到 JSON → 真正的进程错
            snippet_out = raw_out.strip()[:300]
            snippet_err = raw_err.strip()[:300]
            raise RuntimeError(
                f"lark-cli 既未返 stdout JSON 也未返 stderr JSON：\n"
                f"  命令：{' '.join(cmd)}\n"
                f"  退出码：{proc.returncode}\n"
                f"  stdout 前 300 字符：{snippet_out!r}\n"
                f"  stderr 前 300 字符：{snippet_err!r}"
            )

        if not isinstance(payload, dict) or "ok" not in payload:
            raise RuntimeError(
                f"lark-cli 返回 JSON 但缺 ok 字段：\n"
                f"  命令：{' '.join(cmd)}\n"
                f"  stdout：{raw_out[:500]}\n"
                f"  stderr：{raw_err[:500]}"
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
        """lark-cli auth status（注意：auth status 不接受 --format flag，是特例）。

        lark-cli 这个命令**返的 JSON 没有 ok 字段**（裸 {appId, identities, ...}），
        直接把整个返回当作 data 用。
        """
        # _run 会因为缺 ok 抛 RuntimeError；这里单独走一遍特殊解析
        self._assert_bin_exists()
        proc = subprocess.run(
            [self.lark_bin, "auth", "status"],
            capture_output=True, text=False, timeout=15,
        )
        raw_out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        raw_err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        # auth status 通常打到 stderr
        for source in (raw_out, raw_err):
            if not source.strip():
                continue
            try:
                payload = json.loads(source)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue
        raise RuntimeError(
            f"lark-cli auth status 未返合法 JSON：\n"
            f"  stdout：{raw_out[:300]}\n"
            f"  stderr：{raw_err[:300]}"
        )

    def list_wiki_spaces(self, public_only: bool = False) -> List[dict]:
        """lark-cli wiki +space-list --format json --page-all。

        返回 [{name, space_id, visibility, open_sharing, ...}, ...]

        Args:
            public_only: 只返 visibility=="public" 的 space。
                私有 wiki Lark CLI 读不到内容，提前过滤能省时间 + 避免一堆 warn。

        visibility 字段含义：
            - "public"  企业公开（脚本能读内容）
            - "private" 私有（脚本大概率 fetch 失败）
        open_sharing 是另一个维度（"open"=对外开放给所有人，
        "closed"=只对企业成员），不要和 visibility 混淆。
        """
        spaces = self._run(
            ["wiki", "+space-list", "--format", "json", "--page-all"],
            timeout=30,
        ).get("spaces", []) or []

        if not public_only:
            return spaces

        before = len(spaces)
        public = [s for s in spaces if s.get("visibility") == "public"]
        skipped = before - len(public)
        # 把 skipped 数通过 print 让 main() 知道
        # （main() 拿到 list 后用 len() 比较即可，这里不返元组保持简单）
        return public

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


def build_export_dirname(space_name: str, space_id: str) -> str:
    """导出目录名：<sanitized_space_name>_<space_id前8位>。

    加 space_id 前缀防：
    - 不同 space 重名（虽然概率低）
    - Windows 上中文目录 + Git Bash 显示差异
    - 用户改 space_name 后落盘目录还能识别
    """
    s = sanitize_filename(space_name)
    prefix = (space_id or "no000000")[:8]
    # 如果 sanitize 后变 'untitled'，至少还有 id 前缀可识别
    return f"{s}_{prefix}"


# ========== 同步流程 ==========

# 跟 app/data/loader.py:27 SUPPORTED_EXTENSIONS 对齐。
# 脚本只产 .md（飞书 markdown），但留 .markdown/.txt 兜底。
_SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
_MIME_MAP = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}

# ========== 增量同步状态（v0.6 新增） ==========

# manifest 记录"上次同步时每篇文档的内容指纹"。
# 增量同步核心：fetch 内容后算 hash 和 manifest 比——
# 没变 → 跳过 upload（省掉重新 embed 的 API 调用）
# 变了 → upload（服务端 add_chunks 先删后加，自动覆盖旧版）
# key 用导出文件名：它同时是 upload 的 source_name 和服务端 _source_index 的 key，
# 三处对齐，删除/覆盖才能对得上号
_MANIFEST_PATH = Path("data/manifest.json")


def _content_hash(text: str) -> str:
    """算文档内容的 SHA256 指纹。

    为什么用 hash 而不是直接比较文本：
    - 飞书 markdown 可能几百 KB，逐字符比较慢
    - hash 定长 64 字符，比较 O(1)
    - 内容改一个字，hash 完全不同（雪崩效应）——宁可误判"变了"也不会漏判
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_manifest() -> dict:
    """读 manifest。不存在或损坏时返回空结构（视为首次同步，走全量）。

    为什么损坏时不报错而是返回空：
    宁可全量重传（多花点 embed 钱），不可误跳过（旧版内容悄悄留在库里）。
    增量同步的第一原则：失败方向必须选"多干活"而不是"少干活"。
    """
    if not _MANIFEST_PATH.exists():
        return {"last_sync": None, "documents": {}}
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_sync": None, "documents": {}}


def _save_manifest(manifest: dict) -> None:
    """写 manifest，自动更新 last_sync 时间戳。"""
    manifest["last_sync"] = datetime.now(timezone.utc).isoformat()
    _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="包含私有 wiki（默认只同步企业公开 visibility=public 的 space）。"
             "user 身份登录后私有 wiki 也能读到内容，默认排除，避免个人文档误入知识库。",
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
        # 先列全部，再按 --public-only 过滤（这样能告诉用户跳过了多少）
        try:
            all_spaces = cli.list_wiki_spaces()
        except LarkCLIError as e:
            print(f"❌ 列 wiki 失败（业务错）：{e}")
            print("   可能 scope 不够 → 重新跑 lark-cli auth login --recommend")
            return 1
        except RuntimeError as e:
            print(f"❌ 列 wiki 失败（命令错）：{e}")
            return 1

        if args.include_private:
            spaces = all_spaces
            print(f"✓ 找到 {len(spaces)} 个 wiki space（--include-private：包含私有）")
        else:
            # 默认只同步企业公开：user 身份能列出所有有权限的 space（含个人私有），
            # 不过滤会把个人文档也灌进知识库。bot 身份时代"只见公开"是身份
            # 造成的假象，现在必须显式过滤。
            spaces = [s for s in all_spaces if s.get("visibility") == "public"]
            skipped = len(all_spaces) - len(spaces)
            print(f"✓ 共找到 {len(all_spaces)} 个 space，默认只同步企业公开："
                  f"保留 {len(spaces)} 个，跳过 {skipped} 个私有"
                  f"（想包含私有加 --include-private）")

    if not spaces:
        print("ℹ️  没有 wiki 可同步。可能原因：")
        print("   1. space 都是私有的（默认只同步企业公开；想包含私有加 --include-private）")
        print("   2. 没给 Lark CLI 正确 scope（重新跑 lark-cli auth login --recommend）")
        return 0

    # 4. 逐 space 列 nodes + fetch + 落盘
    output_dir.mkdir(parents=True, exist_ok=True)
    written_files: List[Path] = []
    by_type: dict = {}            # {obj_type: count}
    skipped_by_type: dict = {}    # {obj_type: [title, ...]}

    # v0.6 新增：读同步状态
    # docs_meta 结构：{文件名: {"content_hash", "space_id", "obj_token", "title", "synced_at"}}
    # 它是"上次同步后，每篇文档在向量库里的真实状态"
    manifest = _load_manifest()
    docs_meta = manifest["documents"]
    unchanged_count = 0  # 内容没变、跳过上传的文档数

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
            dname = build_export_dirname(sname, sid)
            out_path = output_dir / dname / fname
            # v0.6：先比对后落盘——内容没变则完全零操作（不落盘、不上传、不记账）
            # 跳过落盘是安全的：内容没变 = 本地文件上次写的就是这个内容，重写是纯重复
            # （唯一边界：本地文件被手动删了但内容没变 → 不会重新生成。
            #   lark-exports 只是 debug 备份，可接受）
            new_hash = _content_hash(md)
            old = docs_meta.get(fname)
            if old and old.get("content_hash") == new_hash:
                unchanged_count += 1
                print(f"    ⊘ {title}（内容没变，跳过落盘和 upload）")
                continue

            # 内容变了（或新文档）：落盘 + 记账 + 进上传列表
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md, encoding="utf-8")
            except OSError as e:
                print(f"    ✗ {title} 写盘失败：{e}")
                continue

            # 先记新 hash 进 manifest 候选
            # 注意是"先记账"——上传失败后要回滚（见上传后处理），不能漏。
            # 原则：manifest 只能记"已成功入库"的状态，这里只是候选
            docs_meta[fname] = {
                "content_hash": new_hash,
                "space_id": sid,
                "obj_token": obj_token,
                "title": title,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
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
        # v0.6：dry-run 只落盘不更新 manifest——没真正上传成功，
        # 记了 hash 的话下次 sync 会误判"没变"而跳过
        print("  (dry-run 模式，跳过上传，manifest 不更新)")
        return 0

    if not written_files:
        print("  没有可上传的文件")
        return 0

    # 6. 上传
    print(f"\n--- 上传 {len(written_files)} 个文件到 {args.service_url} ---")
    result = upload_to_rag(args.service_url, written_files)

    # v0.6 新增：上传失败的文档回滚 manifest 记录
    # 为什么必须回滚：fetch 时已把新 hash 记进 docs_meta，上传失败不撤销的话，
    # 下次 sync 一比 hash——"没变"→ 跳过 → 该文档永远不同步。
    # 这是增量同步最经典的坑：manifest 是"入库成功"的账本，失败的账要销
    if result["failed"]:
        failed_names = {f["file"] for f in result["failed"]}
        for fname in failed_names:
            if docs_meta.pop(fname, None) is not None:
                print(f"  ⚠️  {fname} 上传失败，manifest 已回滚（下次 sync 重试）")

    # 全部成功才把候选账本落盘
    _save_manifest(manifest)

    print(f"\n=== 完成 ===")
    print(f"  成功：{result['uploaded']}")
    print(f"  失败：{len(result['failed'])}")
    print(f"  未变更（跳过 upload）：{unchanged_count}")
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

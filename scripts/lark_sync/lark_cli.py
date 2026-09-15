"""Lark CLI 子进程适配器。

把 [lark-cli](https://github.com/larksuite/cli) 的命令行封装成 Python 方法：
上层只看到 dict / list / str，不用关心子进程、编码、ok 信封这些细节。

真实命令：
    lark-cli auth status --format json
    lark-cli wiki +space-list --format json --page-all
    lark-cli wiki +node-list  --space-id <sid> --format json --page-all
    lark-cli docs  +fetch     --doc <obj_token> --doc-format markdown --scope full
"""
import json
import os
import shutil
import subprocess
from typing import List, Optional


class LarkCLIError(RuntimeError):
    """Lark CLI 进程成功但返回 {"ok": false, ...}。

    跟 subprocess 级 RuntimeError 区分，方便上层针对性提示用户
    （比如「请重新登录」「scope 不够」），而不是笼统报命令失败。
    """

    def __init__(self, message: str, payload: Optional[dict] = None):
        super().__init__(message)
        self.payload = payload  # 原始 {"ok": false, "error": ...}


class LarkCLI:
    """Lark CLI 的最小封装。

    所有命令不接受位置参数，必须用 flag（--node-token / --doc / --space-id）。
    返回统一外壳 {"ok": bool, "data": {...}}，本封装已做 ok 校验，
    上层只看到 dict / 抛 LarkCLIError。
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
        # 显式 --as user：同步读的是"用户的" wiki/文档，bot 身份看不到
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

        visibility 字段含义：
            - "public"  企业公开（脚本能读内容）
            - "private" 私有
        open_sharing 是另一个维度（"open"=对外开放给所有人，
        "closed"=只对企业成员），不要和 visibility 混淆。
        """
        spaces = self._run(
            ["wiki", "+space-list", "--format", "json", "--page-all"],
            timeout=30,
        ).get("spaces", []) or []

        if not public_only:
            return spaces
        return [s for s in spaces if s.get("visibility") == "public"]

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

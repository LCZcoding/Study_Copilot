"""增量同步账本（manifest.json）。

manifest 记录"上次成功同步时每篇文档的内容指纹"。增量同步核心：
fetch 内容后算 hash 和 manifest 比——
- 没变 → 跳过 upload（省掉重新 embed 的 API 调用）
- 变了 → upload（服务端 add_chunks 先删后加，自动覆盖旧版）
- 飞书侧没了 → 删除传播（见 sync.py）

key 用导出文件名：它同时是 upload 的 source_name 和服务端
_source_index 的 key，三处对齐，删除/覆盖才能对得上号。
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

# 默认账本位置（相对进程 CWD，与 data/rag.db 同目录）
DEFAULT_MANIFEST_PATH = Path("data/manifest.json")

PathLike = Union[str, Path]


def content_hash(text: str) -> str:
    """算文档内容的 SHA256 指纹。

    为什么用 hash 而不是直接比较文本：
    - 飞书 markdown 可能几百 KB，逐字符比较慢
    - hash 定长 64 字符，比较 O(1)
    - 内容改一个字，hash 完全不同（雪崩效应）——宁可误判"变了"也不会漏判
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def empty_manifest() -> dict:
    """空账本结构（last_sync + documents 两段，别让上层自己拼 key）。"""
    return {"last_sync": None, "documents": {}}


def load_manifest(path: PathLike = DEFAULT_MANIFEST_PATH) -> dict:
    """读 manifest。不存在或损坏时返回空结构（视为首次同步，走全量）。

    为什么损坏时不报错而是返回空：
    宁可全量重传（多花点 embed 钱），不可误跳过（旧版内容悄悄留在库里）。
    增量同步的第一原则：失败方向必须选"多干活"而不是"少干活"。
    """
    manifest_path = Path(path)
    if not manifest_path.exists():
        return empty_manifest()
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_manifest()


def save_manifest(
    manifest: dict,
    path: PathLike = DEFAULT_MANIFEST_PATH,
) -> None:
    """写 manifest，自动更新 last_sync 时间戳（UTC）。"""
    manifest["last_sync"] = datetime.now(timezone.utc).isoformat()
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

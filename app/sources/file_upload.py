"""文件上传数据源（v0.1 等价升级到 SourceConnector 接口）。

v0.1 行为：用户通过 HTTP /upload 上传文件 → 立即入库
v0.5-2 变化：上传文件先存到 FileUploadConnector → 用户手动 / 启动时触发同步

为什么拆分：v0.1 的"上传即入库"耦合了上传和索引，v0.5-2 解耦：
- 上传：仅把字节存到 connector 的内存字典
- 同步：connector 把内存中的文件 fetch_documents → chunker → embedder → retriever
- 这让"多源"成为可能：飞书同步也是走 connector.fetch_documents()
"""

from datetime import datetime
from typing import Dict, List

from app.data.loader import DocumentLoadError, load_document
from .base import Document, SourceConnector, SourceMeta


class FileUploadConnector(SourceConnector):
    """文件上传数据源。

    v0.5-2 限制：
    - 文件存在内存里（重启数据丢，跟 v0.1 一样）
    - 没有真实的增量同步概念
    - 多文档管理接口可以 list / delete 文件名

    v0.5-4 改进：
    - 接 SQLite 持久化文件内容（或只持久化文件路径）
    - 真正的增量同步（基于 mtime）
    """

    source_type = "file"

    def __init__(self):
        # 内存存储：filename → bytes
        # Java 类比：类似一个 ConcurrentHashMap<String, byte[]>
        self._files: Dict[str, bytes] = {}

    # ========== 文件管理 API（直接给 chat.py / documents.py 用） ==========

    def add_file(self, filename: str, content: bytes) -> None:
        """用户上传文件后调用：把文件存到内存。

        v0.5-2 简化：同名文件直接覆盖（不报错）。
        v0.5-4 可改成版本号机制。
        """
        self._files[filename] = content

    def has_file(self, filename: str) -> bool:
        return filename in self._files

    def delete_file(self, filename: str) -> bool:
        """从内存删除文件（注意：向量库里的 chunks 不会自动删）。

        完整删除需要：
        1. delete_file() → 从内存移除
        2. retriever.delete_source() → 从向量库移除
        调用方负责两步配合。
        """
        return self._files.pop(filename, None) is not None

    def list_filenames(self) -> List[str]:
        return list(self._files.keys())

    # ========== SourceConnector 接口实现 ==========

    async def fetch_documents(self) -> List[Document]:
        """拉取所有上传的文件作为 Document 列表。

        跳过解析失败的文件（v0.5-2 简化：记日志但不抛错）。
        v0.5-4 可改成收集错误后批量报告。
        """
        documents: List[Document] = []
        for filename, content in self._files.items():
            try:
                text = load_document(content, filename)
            except DocumentLoadError:
                # 跳过损坏文件（可能上传时已成功，但解析失败）
                # v0.5-2 简化：静默跳过
                continue

            # 构造 SourceMeta，name 用文件名
            meta = SourceMeta.now(
                source_type=self.source_type,
                source_name=filename,
            )
            documents.append(Document(text=text, meta=meta))

        return documents

    async def sync_changes(self, since: datetime) -> List[Document]:
        """v0.5-2 简化：文件上传数据源没有真正的增量概念（全量=增量）。

        v0.5-4 可基于文件 mtime 实现真正的增量。
        """
        return await self.fetch_documents() 

    async def health_check(self) -> bool:
        """v0.5-2 永远 True（本地内存存储，无网络依赖）。

        保留接口是为了和飞书 / GitHub 等远程数据源保持一致。
        """
        return True
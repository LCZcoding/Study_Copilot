"""数据源抽象层。

v0.5-2 引入：为支持多源（文件 / 飞书 / GitHub 等）做准备。

设计：
- SourceConnector ABC：所有数据源实现统一接口
- Document dataclass：表示一个完整文档（含 source 元信息）
- SourceMeta dataclass：chunks 共享的元数据 
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass #dataclass类似lambok 自动写init和一些其他方法
class SourceMeta:
    """数据源元信息（所有 chunks 共享同一份 metadata）。

    字段说明：
    - source_type：枚举（"file" / "feishu" / "github" / "web"）
      v0.5-2 仅 "file" 实现，其他类型等后续 Phase
    - source_name：人类可读标识
      - file：文件名（如 "raft.pdf"）
      - feishu：文档标题（如 "Raft 共识算法笔记"）
    - source_url：可访问的外部链接（v0.5-2 仅 feishu 用）
    - sync_version：每次同步自增（用于检测内容变化）
    - ingested_at：ISO 格式时间戳 
    """

    source_type: str
    source_name: str
    source_url: Optional[str] = None
    sync_version: int = 1
    ingested_at: Optional[str] = None

    def to_dict(self) -> dict:
        """转成 dict 存到 LangChain metadata（必须 JSON 序列化兼容）。

        为什么需要 dict：LangChain 的 Document.metadata 字段要求 dict，
        检索时它会把整个 dict 存到向量库里。我们需要保持字段可序列化。
        """
        return {
            "source_type": self.source_type,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "sync_version": self.sync_version,
            "ingested_at": self.ingested_at,
        }

    @classmethod
    def now(cls, source_type: str, source_name: str, **kwargs) -> "SourceMeta":
        """工厂方法：自动填当前时间戳。""" # 「工厂方法」不直接 new 对象，而是调一个专门的方法来帮你创建——把"怎么造"封在方法里。
        return cls(
            source_type=source_type,
            source_name=source_name,
            ingested_at=datetime.now(timezone.utc).isoformat(),
            **kwargs,#**kwargs：透传给构造器的额外字段，让调用者不用改方法签名就能扩展
        )

    @property
    def key(self) -> str:
        """唯一标识：`type:name`，用于 list/delete 文档。

        为什么用 `:` 分隔：URL/路径里都不会出现这个字符，避免冲突。
        """
        return f"{self.source_type}:{self.source_name}"


@dataclass
class Document:
    """一个完整的文档（chunker 之前的状态）。

    跟 LangChain 的 Document 不一样：
    - LangChain Document 是 chunk 级别（page_content + metadata）
    - 我们的 Document 是文档级别（text 完整 + meta 元信息）
    - chunker 后续会把 Document 切成多个 LangChain Document（带 metadata）
    """

    text: str  # 文档全文（已从 PDF/MD/HTML 等转成纯文本）
    meta: SourceMeta

    @property
    def key(self) -> str:
        """等价于 meta.key。"""
        return self.meta.key


class SourceConnector(ABC):
    """数据源抽象接口。

    v0.5-2 实现：FileUploadConnector
    v0.5-2 实现：FeishuConnector（下一 session）
    未来扩展：GitHubConnector、WebCrawlerConnector

    设计原则：
    - fetch_documents 拉取所有（首次同步）
    - sync_changes 拉取增量（v0.5-2 简化为等同 fetch）
    """

    # 子类定义：'file' / 'feishu' / 'github' / 'web'
    source_type: str = "abstract"

    @abstractmethod
    async def fetch_documents(self) -> List[Document]:
        """拉取所有文档（首次同步用）。

        返回 List[Document]，每个 Document 含完整文本 + 元信息。
        后续 chunker 会把每个 Document 切成 chunks。
        """
        raise NotImplementedError

    @abstractmethod
    async def sync_changes(self, since: datetime) -> List[Document]:
        """拉取自 since 之后的变更（增量同步用）。

        Args:
            since: 上次同步时间

        Returns:
            新增/更新的文档列表

        v0.5-2 简化：FileUploadConnector 没有真正的"增量"概念，
        实现等同 fetch_documents。v0.5-4 接入飞书时会实现真正的增量。
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查：能否连接到数据源（鉴权 / 网络）。"""
        raise NotImplementedError
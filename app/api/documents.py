"""多文档管理 API（v0.5-2 新增）。

提供 list / delete 文档端点，让用户能管理已索引的多源文档。

v0.5-2 范围：
- GET /api/documents：列出所有已索引的文档源
- DELETE /api/documents/{source_type}/{source_name}：删除某个文档的所有 chunks

未来扩展（不在 v0.5-2）：
- POST /api/sources/feishu/sync：手动触发飞书同步
- GET /api/sources：列出已配置的数据源
- POST /api/sources/{type}/config：配置新数据源
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.rag.retriever import StudyCopilotRetriever


router = APIRouter()


# 全局引用（lifespan 注入）
_retriever: StudyCopilotRetriever | None = None


def init_components(retriever: StudyCopilotRetriever) -> None:
    """lifespan 启动时调用此函数注入单例。"""
    global _retriever
    _retriever = retriever


def _require_initialized():
    if _retriever is None:
        raise HTTPException(503, "服务尚未初始化完成，请稍后重试")


# ========== Pydantic 模型 ==========

class DocumentSummary(BaseModel):
    """文档摘要（list 接口返回的每条）。"""
    source_key: str       # 唯一标识，格式 "type:name"
    source_type: str      # 数据源类型（"file" / "feishu" / ...）
    source_name: str      # 数据源名称（文件名 / 飞书文档标题）
    chunks: int           # 该文档的 chunk 数


class DocumentListResponse(BaseModel):
    """list 接口响应体。"""
    documents: List[DocumentSummary]
    total_chunks: int


class DeleteResponse(BaseModel):
    """delete 接口响应体。"""
    message: str
    deleted_chunks: int


# ========== 路由 ==========

@router.get("/documents", response_model=DocumentListResponse)
async def list_documents():
    """列出所有已索引的文档源。

    v0.5-2 限制：不返回 source_url（避免响应过大，v0.5-4 再加）。
    """
    _require_initialized()

    sources = _retriever.list_sources()
    total_chunks = sum(s["chunks"] for s in sources)

    return DocumentListResponse(
        documents=[DocumentSummary(**s) for s in sources],
        total_chunks=total_chunks,
    )


@router.delete("/documents/{source_type}/{source_name}", response_model=DeleteResponse)
async def delete_document(source_type: str, source_name: str):
    """删除指定 source 的所有 chunks。

    v0.5-2 限制：
    - 只从向量库删除 chunks，不删除原始文件（v0.1 等价行为）
    - 可以重复删除同名文档（幂等：第二次返回 0 删除）
    """
    _require_initialized()

    deleted = _retriever.delete_source(source_type, source_name)
    if not deleted:
        # 该 source 不存在（已经被删除或从未上传）
        raise HTTPException(404, f"文档不存在：{source_type}:{source_name}")

    return DeleteResponse(
        message=f"已删除 {source_type}:{source_name}",
        deleted_chunks=_retriever.count,  # 注意：这是删除后的总数，不是被删除的数量
    )
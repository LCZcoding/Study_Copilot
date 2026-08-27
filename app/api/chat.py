"""FastAPI 路由层：上传文档 + 问答 + 健康检查。
为啥叫路由层？
v0.1 单文档（重启数据丢）：模块级单例 retriever + embedder + llm，
FastAPI lifespan 启动时初始化，关闭时清理。

Java 类比：类似 Spring 的 @RestController + 全局 @Service 单例注入。
"""

from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.llm.base import ChatMessage
from app.core.llm.router import LLMRouter
from app.data.loader import DocumentLoadError, load_document
from app.rag.chunker import chunk_document
from app.rag.embedder import SiliconFlowBGEEmbeddings
from app.rag.prompts import (
    RAG_SYSTEM_PROMPT_WITH_CONTEXT,
    RAG_SYSTEM_PROMPT_NO_CONTEXT,
    RAG_USER_TEMPLATE,
)
from app.rag.retriever import StudyCopilotRetriever
from app.sources.file_upload import FileUploadConnector


router = APIRouter() # 收集路由


# ========== 模块级单例（FastAPI 推荐模式之一，简单直接） ==========
# v0.1 用模块级单例就够了——所有请求共享同一个实例。
# v0.5+ 多文档管理时改成 app.state 注入会更灵活。
# 这些会在 lifespan 里被替换成真实实例（避免循环依赖）。
_retriever: StudyCopilotRetriever | None = None
_embedder: SiliconFlowBGEEmbeddings | None = None
_llm: LLMRouter | None = None
# v0.5-2：文件上传数据源（HTTP 上传的文件暂存在这里）
_file_connector: FileUploadConnector | None = None


def init_components(
    retriever: StudyCopilotRetriever,
    embedder: SiliconFlowBGEEmbeddings,
    llm: LLMRouter,
    file_connector: FileUploadConnector,
) -> None:
    """lifespan 启动时调用此函数注入单例。"""
    global _retriever, _embedder, _llm, _file_connector
    _retriever = retriever
    _embedder = embedder
    _llm = llm
    _file_connector = file_connector


def _require_initialized():
    """内部辅助：确保 lifespan 已经初始化过组件，否则报 503。"""
    if _retriever is None or _embedder is None or _llm is None:
        raise HTTPException(503, "服务尚未初始化完成，请稍后重试")


# ========== Pydantic 模型：请求/响应的数据结构 ==========
class ChatRequest(BaseModel):
    """聊天请求体。"""
    question: str
    top_k: int = 3  # 检索几个相关片段
    min_score: float = 0.3  # 相似度阈值（v0.5-2 D ext：低于此分视为未召回，触发 404）
                        # bge-m3 实测短文档场景：0.3-0.4 区间区分"沾边"和"真正相关"
                        # 0.5 阈值对中等以上文档合理，但对短文档（如 test_smoke 的 50 字符示例）会误杀


class ChatResponse(BaseModel):
    """聊天响应体。"""
    answer: str
    sources: List[str]  # 答案引用的来源文件


class UploadResponse(BaseModel):
    """上传响应体。"""
    chunks: int  # 切出多少段
    total_chars: int  # 文档总字符数
    message: str = "索引建立成功"


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str
    indexed_chunks: int
    components: dict


# ========== 路由 ==========
@router.post("/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """上传文档（PDF / Markdown / TXT）并建立向量索引。

    v0.5-2 流程变化：
    1. 读文件二进制 → 存到 FileUploadConnector（不仅是直接入库）
    2. 调 connector.fetch_documents() 拿到所有待索引的 Document
    3. 每个 Document → 切片 → embed → 入库

    为什么改：v0.1 的"上传即入库"耦合了上传和索引，
    v0.5-2 解耦后，文件上传跟后续飞书等数据源走同一条流水线。
    """
    _require_initialized()

    # FastAPI 的 UploadFile.read() 返回 bytes（跟 Phase 2 的 loader 接口对齐）
    content = await file.read()
    filename = file.filename or "unknown"

    # 1. 把上传的文件存到 FileUploadConnector
    _file_connector.add_file(filename, content)

    # 2. 从 connector 拿所有文档（v0.5-2 简化为只返回刚加的那个，
    # 但接口上跟"全量同步"一致，方便后续接飞书）
    documents = await _file_connector.fetch_documents()

    # 3. 文档切片 → embed → 入库
    total_chars = 0
    total_chunks = 0
    for doc in documents:
        text = doc.text
        total_chars += len(text)

        # 切片
        chunks = chunk_document(text, chunk_size=500, overlap=50)
        if not chunks:
            continue

        # 加 meta 字段（v0.5-2 多源 metadata 已经在 doc.meta 里）
        chunks_with_meta = []
        for c in chunks:
            chunks_with_meta.append({
                **c,
                "source_type": doc.meta.source_type,
                "source_name": doc.meta.source_name,
                "source_url": doc.meta.source_url,
                "sync_version": doc.meta.sync_version,
                "ingested_at": doc.meta.ingested_at,
            })

        # Embedding
        vectors = await _embedder.aembed_documents(
            [c["text"] for c in chunks_with_meta]
        )

        # 入库
        _retriever.add_chunks(chunks_with_meta, vectors)
        total_chunks += len(chunks)

    if total_chunks == 0:
        raise HTTPException(400, "文档内容为空或切片后为空")

    return UploadResponse(
        chunks=total_chunks,
        total_chars=total_chars,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """基于已索引文档做 RAG 问答。

    流程：
        1. embedder.embed_query(question) → query 向量
        2. retriever.search(query_vector, top_k) → 相关 chunks
        3. 组装 prompt（RAG_SYSTEM_PROMPT + context + RAG_USER_TEMPLATE）
        4. llm.chat() → 最终回答
    """
    _require_initialized()

    if not req.question.strip():
        raise HTTPException(400, "问题不能为空")

    # 1. Embedding 问题
    query_vector = await _embedder.aembed_query(req.question)

    # 2. 检索（v0.5-2 返回字段：source_type / source_name / source_url / chunk_id / score）
    # v0.5-2 D ext：传 min_score 过滤低分 chunk，避免"沾边但无关"召回导致 LLM 编造
    results = _retriever.search(
        query_vector, top_k=req.top_k, min_score=req.min_score,
    )
    if not results:
        # 空库或无相关结果（包含"无 chunks 过阈值"的情况）
        if _retriever.count == 0:
            raise HTTPException(404, "没有找到相关文档，请先通过 /upload 上传文档")
        raise HTTPException(
            404,
            f"未找到与问题相关的内容（min_score={req.min_score}）。"
            f"可调低 min_score 或换个说法重试",
        )

    # 3. 组装 Prompt
    # v0.5-2 D ext：根据是否召回到相关 chunk 选不同 prompt
    # - 召回到（results 非空）→ 严格基于参考资料
    # - 没召回（results 空）→ 兜底通用知识 + 开头告知用户
    if results:
        context = "\n\n---\n\n".join(r["text"] for r in results)
        system_prompt = RAG_SYSTEM_PROMPT_WITH_CONTEXT.format(context=context)
    else:
        system_prompt = RAG_SYSTEM_PROMPT_NO_CONTEXT

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(
            role="user",
            content=RAG_USER_TEMPLATE.format(question=req.question),
        ),
    ]

    # 4. 调 LLM 生成答案
    response = await _llm.chat(messages)
    answer = response.content

    # v0.5-2 D ext：兜底模式必须开头告知用户。
    # 用服务层强制加前缀（LLM 不 100% 遵守 prompt 时仍生效）。
    if not results and not answer.startswith("未在知识库中找到"):
        answer = "未在知识库中找到相关内容，以下是通用回答：\n\n" + answer

    # v0.5-2：去重 sources 时改用 source_name 字段（v0.1 用 source）
    sources = list(dict.fromkeys(r["source_name"] for r in results)) #字典key只能有一个

    return ChatResponse(
        answer=answer,
        sources=sources,
    )


@router.get("/health", response_model=HealthResponse)
async def health():
    """健康检查 + 组件状态。"""
    components = {
        "embedder": "ready" if _embedder else "not initialized",
        "retriever": "ready" if _retriever else "not initialized",
        "llm": "ready" if _llm else "not initialized",
    }
    return HealthResponse(
        status="ok" if all(v == "ready" for v in components.values()) else "degraded",
        indexed_chunks=_retriever.count if _retriever else 0,
        components=components,
    )
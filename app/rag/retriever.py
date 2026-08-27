"""向量检索器：在内存中存文档向量，按相似度找 top-k。

设计选择：用 LangChain 的 `InMemoryVectorStore`（企业级标准）做底层存储，
上面包一层我们的 domain 类，方便业务代码调用、隐藏 LangChain 的 API 细节。

为什么 v0.1 用 LangChain 而不自己写 numpy？
- LangChain 是 AI 应用的事实标准接口（跟 Phase 1 用 LangChain Embeddings 配套）
- 自己写 numpy 检索 < 50 行能搞定，但 v0.5+ 接 PGVector 时 LangChain 抽象直接复用
- numpy 实现的"学习价值"在 verify_phase3.py 里保留：手写一遍 + 对比 LangChain 结果

数据流：
    上传文档 → 切片 → embedder.embed_documents(chunks) → retriever.add_chunks(chunks, vectors)
    用户提问 → embedder.embed_query(question) → retriever.search(query_vector, top_k=3)
"""

from typing import Any, Dict, List, Optional

import numpy as np
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore


class StudyCopilotRetriever:
    """学习助手专用检索器，封装 LangChain 的内存向量存储。

    Java 类比：类似 Spring 的 Repository 模式——业务代码调我们的接口，
    底层可以换实现（FAISS / PGVector / Milvus）而业务代码不变。

    v0.5-2 扩展：支持多文档管理
    - list_sources()：返回所有已索引的文档源
    - delete_source()：按 source 删除所有 chunks
    """

    def __init__(self, embedding: Embeddings):
        # 把 LangChain 的 Embeddings 实例传进去，LangChain 会用它做需要 embed 的地方
        # （比如直接传文本搜索时）。我们 v0.1 主要用 search_by_vector，自己控制 embed。
        self.embedding = embedding
        self.vector_store = InMemoryVectorStore(embedding=embedding) # 内存存储，可以比较方便的修改为数据库
        # 记录已添加的 chunk 数量，方便调试和单元测试断言。
        self._count = 0
        # v0.5-2 新增：按 source 索引 LangChain 的 chunk ID
        # 为什么要这个：delete_source 需要知道"这个 source 有哪些 chunk"，才能批量删
        # 结构：{"file:raft.pdf": ["id_1", "id_2", ...], "feishu:xxx": [...]}
        self._source_index: Dict[str, List[str]] = {}

    @property
    def count(self) -> int:
        """当前存储的文档片段数。"""
        return self._count

    def add_chunks(
        self,
        chunks: List[Dict[str, Any]],
        vectors: List[List[float]],
    ) -> None:
        """把切好的文档片段（已 embed）加入向量库。

        v0.5-2 改动：chunks 字典结构变了——
        旧：{"id": int, "text": str, "source": str}
        新：{"id": int, "text": str, "source_type": str, "source_name": str, ...}

        Args:
            chunks: 来自 chunker.chunk_document() 的输出（v0.5-2 多了 meta 字段）
            vectors: 与 chunks 一一对应的 1024 维向量，来自 embedder.embed_documents()

        Raises:
            ValueError: chunks 和 vectors 数量不一致
        """
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks 和 vectors 数量不匹配：{len(chunks)} vs {len(vectors)}"
            )
        if not chunks:
            return

        # v0.5-2：从 chunk 字典抽取元数据，转成 LangChain metadata
        # LangChain 要求 metadata 是 dict，所以我们手动构造
        metadatas = [
            {
                "source_type": c.get("source_type", "file"),
                "source_name": c.get("source_name", "unknown"),
                "source_url": c.get("source_url"),
                "sync_version": c.get("sync_version", 1),
                "ingested_at": c.get("ingested_at"),
                "chunk_id": c["id"],
            }
            for c in chunks
        ]
        texts = [c["text"] for c in chunks] #得到所有text存到列表

        # LangChain 的 add_texts 接受预计算的 embeddings 参数，避免重复 embed。
        # 它返回每个 chunk 的内部 ID（UUID 形式），我们需要存起来用于后续删除。
        ids = self.vector_store.add_texts(
            texts=texts,
            metadatas=metadatas,
            embeddings=vectors,
        )

        # 更新 source 索引：每个 source_key 映射到它的所有 chunk ID
        for chunk, chunk_id in zip(chunks, ids): # zip打包成元组
            source_key = f"{chunk.get('source_type', 'file')}:{chunk.get('source_name', 'unknown')}"
            self._source_index.setdefault(source_key, []).append(chunk_id)

        self._count += len(chunks)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 3,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """按向量相似度找 top-k 个文档片段。

        v0.5-2 改动：返回字段从 "source" 改为 "source_name",
        同时新增 "source_type" 和 "source_url" 字段。

        v0.5-2 D ext 改动：新增 min_score 过滤。
        召回阶段按 cosine 相似度排，低分的 chunk 通常"沾边但无关"，
        喂给 LLM 会导致 LLM 抓到片段编造。低于 min_score 的直接丢弃，
        让上层返 404 / "未找到相关"。

        Args:
            query_vector: 问题向量（已 embed）
            top_k: 候选数（先取 top_k，再按 min_score 过滤）
            min_score: 相似度阈值（None = 不过滤，bge-m3 实测 0.3-0.85 区间）

        Returns:
            列表，每项是 {
                "text": str,
                "source_type": str,
                "source_name": str,
                "source_url": str | None,
                "chunk_id": int,
                "score": float,
            }
        """
        if self._count == 0:
            return []  # 空库直接返回空，不报错（调用方决定是否提示用户）

        results = self.vector_store.similarity_search_with_score_by_vector(
            embedding=query_vector,
            k=top_k,
        )

        # v0.5-2 D ext：按 min_score 过滤（先用 top_k 拉够，再裁剪）
        if min_score is not None:
            results = [(d, s) for d, s in results if s >= min_score]

        # v0.5-2：展开 LangChain metadata 为完整 dict
        return [
            {
                "text": doc.page_content,
                "source_type": doc.metadata.get("source_type", "file"),
                "source_name": doc.metadata.get("source_name", "unknown"),
                "source_url": doc.metadata.get("source_url"),
                "chunk_id": doc.metadata.get("chunk_id", -1),
                "score": float(score),
            }
            for doc, score in results
        ]

    def search_by_text(
        self,
        query_text: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """便捷方法：传文本，内部自动 embed 再搜。

        业务代码（如 FastAPI 接口）通常用这个，传纯文本就行。
        """
        query_vector = self.embedding.embed_query(query_text)
        return self.search(query_vector, top_k=top_k)

    def clear(self) -> None:
        """清空向量库（用于测试或多文档管理场景）。"""
        # LangChain 0.3 的 InMemoryVectorStore 没有直接 clear 方法，重新 new 一个最干净
        self.vector_store = InMemoryVectorStore(embedding=self.embedding)
        self._count = 0
        self._source_index = {}

    # ========== v0.5-2 多文档管理 ==========

    def list_sources(self) -> List[Dict[str, Any]]:
        """列出所有已索引的文档源。

        Returns:
            列表，每项是 {"source_key": str, "source_type": str, "source_name": str, "chunks": int}

        调用方（如 documents API）拿到这个列表展示给用户。
        """
        result = []
        for source_key, chunk_ids in self._source_index.items():
            # source_key 格式是 "type:name"，拆开
            parts = source_key.split(":", 1) # 1表示maxsplit，只切一个
            if len(parts) == 2:
                source_type, source_name = parts
            else:
                source_type, source_name = "unknown", source_key
            result.append({
                "source_key": source_key,
                "source_type": source_type,
                "source_name": source_name,
                "chunks": len(chunk_ids),
            })
        return result

    def delete_source(self, source_type: str, source_name: str) -> bool:
        """按 source 删除所有 chunks。

        Args:
            source_type: 数据源类型（"file" / "feishu" / ...）
            source_name: 数据源名称（文件名 / 飞书文档标题）

        Returns:
            True 表示成功删除，False 表示该 source 不存在

        实现细节：调用 LangChain 的 delete(ids) 批量删除，再用 pop 清空索引。
        """
        source_key = f"{source_type}:{source_name}"
        chunk_ids = self._source_index.pop(source_key, [])
        if not chunk_ids:
            return False  # 该 source 不存在

        # 调 LangChain 的 delete 方法（InMemoryVectorStore 支持）
        try:
            self.vector_store.delete(ids=chunk_ids)
        except AttributeError:
            # 兼容：如果 LangChain 版本不支持 delete，整个库重建
            self.clear()
            return True

        self._count -= len(chunk_ids)
        return True


# ========== 教学辅助：手写 numpy 实现（仅供学习，不被 retriever 使用） ==========
def cosine_similarity_numpy(vec_a: List[float], vec_b: List[float]) -> float:
    """手写 cosine 相似度（numpy 版），展示底层的数学。

    cosine 相似度 = (A · B) / (||A|| × ||B||)
    几何含义：两个向量的夹角余弦值，取值 [-1, 1]
    - 1 表示完全同向（最相似）
    - 0 表示正交（无关）
    - -1 表示完全反向（最不相似）

    对文本 embedding 来说，分数通常在 [0.3, 0.9] 区间，< 0.3 基本不相关。

    Java 类比：这是经典机器学习公式，sklearn.metrics.pairwise.cosine_similarity
    底层就是这个公式。
    """
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)

    # 点积：np.dot(a, b) 或 a @ b
    dot_product = np.dot(a, b)

    # L2 范数（向量长度）：np.linalg.norm
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    # 防止除以 0（虽然 normalize 后的向量 norm 是 1，但保险起见）
    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(dot_product / (norm_a * norm_b))


def batch_cosine_similarity_numpy(
    query_vec: List[float],
    matrix: np.ndarray,
) -> np.ndarray:
    """批量计算 query 与 matrix 中所有向量的 cosine 相似度。

    当 retriever 里有 N 个向量，要算 N 次相似度，用 for 循环会慢。
    矩阵运算一次搞定：scores = matrix @ query_vec（因为向量已 L2 normalize）

    这是 numpy 高效的关键：用矩阵运算代替 Python for 循环。
    千级向量 < 1ms，万级 < 10ms。
    """
    # matrix shape: (N, 1024), query_vec shape: (1024,)
    # 结果 shape: (N,)
    query = np.array(query_vec, dtype=np.float32)
    # @ 是矩阵乘，np.dot 等价
    scores = matrix @ query
    return scores
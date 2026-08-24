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
    """

    def __init__(self, embedding: Embeddings):
        # 把 LangChain 的 Embeddings 实例传进去，LangChain 会用它做需要 embed 的地方
        # （比如直接传文本搜索时）。我们 v0.1 主要用 search_by_vector，自己控制 embed。
        self.embedding = embedding
        self.vector_store = InMemoryVectorStore(embedding=embedding)
        # 记录已添加的 chunk 数量，方便调试和单元测试断言。
        self._count = 0

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

        Args:
            chunks: 来自 chunker.chunk_document() 的输出，格式 [{"id": int, "text": str}, ...]
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

        # 转成 LangChain 的 Document 对象。metadata 用来存 source 和 chunk_id，
        # 检索时能从 Document 里拿回这些信息用于溯源（告诉用户答案来自哪个文件哪一段）。
        documents = [
            Document(
                page_content=c["text"],
                metadata={
                    "source": c.get("source", "unknown"),
                    "chunk_id": c["id"],
                },
            )
            for c in chunks
        ]
        # LangChain 的 add_texts 接受预计算的 embeddings 参数，避免重复 embed。
        # embeddings 参数是 List[List[float]]，跟 vectors 一致。
        self.vector_store.add_texts(
            texts=[d.page_content for d in documents],
            metadatas=[d.metadata for d in documents],
            embeddings=vectors,
        )
        self._count += len(documents)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """按向量相似度找 top-k 个文档片段。

        Args:
            query_vector: 用户问题的 1024 维向量（embedder.embed_query() 输出）
            top_k: 返回几个最相关的片段

        Returns:
            列表，每项是 {"text": str, "source": str, "chunk_id": int, "score": float}
            按相似度从高到低排序（score 越大越相关，LangChain 用的是距离的负值或余弦值，详见内部注释）
        """
        if self._count == 0:
            return []  # 空库直接返回空，不报错（调用方决定是否提示用户）

        # similarity_search_with_score_by_vector 是 LangChain 的标准方法，
        # 返回 List[Tuple[Document, float]]。第二个值是 score，
        # LangChain 内部用的是"距离的负值"，数值越大越相似。
        results = self.vector_store.similarity_search_with_score_by_vector(
            embedding=query_vector,
            k=top_k,
        )

        # 把 LangChain 的 Document 转成我们的 dict 格式，方便业务层使用。
        return [
            {
                "text": doc.page_content,
                "source": doc.metadata.get("source", "unknown"),
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
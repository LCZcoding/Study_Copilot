"""Phase 3 验证脚本。

测试 Embedding 和向量检索：
1. Embedder（需要 SILICONFLOW_API_KEY，否则跳过）
2. numpy cosine 相似度数学函数（无依赖）
3. Retriever 用 mock 向量跑通（无依赖）
4. 完整集成（需要 SILICONFLOW_API_KEY，否则跳过）

运行：uv run python scripts/verify_phase3.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Windows GBK 兼容
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from langchain_core.embeddings import Embeddings

from app.core.config import config  # noqa: F401  # 触发 .env 加载
from app.rag.chunker import chunk_document
from app.rag.embedder import SiliconFlowBGEEmbeddings
from app.rag.retriever import (
    StudyCopilotRetriever,
    batch_cosine_similarity_numpy,
    cosine_similarity_numpy,
)


FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def has_siliconflow_key() -> bool:
    key = os.getenv("SILICONFLOW_API_KEY", "")
    return bool(key and key != "your_siliconflow_api_key_here")


# ============ 测试 1：Embedder（需要 API key） ============
async def test_embedder_real() -> list:
    """真实 API 调用：embed 两段测试文本。"""
    section("测试 1: SiliconFlow bge-m3 Embedding（需要 API key）")
    if not has_siliconflow_key():
        print("  [SKIP] 未设置 SILICONFLOW_API_KEY")
        print("         注册地址：https://siliconflow.cn/")
        print("         设置后重跑会自动启用本测试")
        return []

    embeddings = SiliconFlowBGEEmbeddings(api_key=os.environ["SILICONFLOW_API_KEY"])
    texts = [
        "Raft 是一种一致性算法。",
        "Paxos 是另一种一致性算法。",
    ]
    vectors = await embeddings.aembed_documents(texts)
    print(f"  embed {len(texts)} 个文本")
    print(f"  每个向量维度：{len(vectors[0])}")
    print(f"  前 5 维：{[round(v, 4) for v in vectors[0][:5]]}")
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024, "bge-m3 输出应该是 1024 维"
    print("  [PASS] 真实 embed 成功，向量维度 1024")
    return vectors


# ============ 测试 2：numpy cosine 数学 ============
def test_cosine_math() -> None:
    """测试 numpy 实现的 cosine 相似度函数。"""
    section("测试 2: numpy cosine 相似度（纯数学，无 API 依赖）")

    # 2a. 相同向量 → 相似度 = 1
    v1 = [1.0, 0.0, 0.0]
    score = cosine_similarity_numpy(v1, v1)
    print(f"  v1 vs v1 (相同): {score:.4f}")
    assert abs(score - 1.0) < 1e-6

    # 2b. 正交向量 → 相似度 = 0
    v2 = [0.0, 1.0, 0.0]
    score = cosine_similarity_numpy(v1, v2)
    print(f"  v1 vs v2 (正交): {score:.4f}")
    assert abs(score) < 1e-6

    # 2c. 反向向量 → 相似度 = -1
    v3 = [-1.0, 0.0, 0.0]
    score = cosine_similarity_numpy(v1, v3)
    print(f"  v1 vs v3 (反向): {score:.4f}")
    assert abs(score - (-1.0)) < 1e-6

    # 2d. 随机向量的批量相似度
    np.random.seed(42)
    matrix = np.random.randn(100, 1024).astype(np.float32)
    query = np.random.randn(1024).astype(np.float32)
    scores = batch_cosine_similarity_numpy(query.tolist(), matrix)
    print(f"  批量相似度：100 个向量 vs query，shape={scores.shape}")
    print(f"  分数范围：[{scores.min():.4f}, {scores.max():.4f}]")
    assert scores.shape == (100,)
    print("  [PASS] cosine 数学函数正确")


# ============ 测试 3：Retriever 用 mock 向量 ============
def test_retriever_mock() -> None:
    """用程序生成的 mock 向量测试 retriever。

    模拟场景：3 篇文档，每篇 1 个 chunk。query 向量 = 第一篇 chunk 的向量，
    期望检索结果第一就是它。
    """
    section("测试 3: Retriever（mock 向量，无 API 依赖）")

    # 用一个 fake embedding（按文本长度生成简单向量），
    # 让"文本相似"和"向量相似"有可预测的对应关系。
    class FakeEmbedding(Embeddings):
        def embed_documents(self, texts):
            # 把文本按字符 ord 求和 → 转成 1024 维向量（确定性、可复现）
            return [_fake_vec(t) for t in texts]

        def embed_query(self, text):
            return _fake_vec(text)

        async def aembed_documents(self, texts):
            return self.embed_documents(texts)

        async def aembed_query(self, text):
            return self.embed_query(text)

    retriever = StudyCopilotRetriever(embedding=FakeEmbedding())

    # 准备 3 篇文档，每篇 1 个 chunk
    chunks = [
        {"id": 0, "text": "Raft 选举领导者通过投票", "source": "raft_intro.md"},
        {"id": 1, "text": "Paxos 也是一致性算法", "source": "paxos_intro.md"},
        {"id": 2, "text": "Python 是一门编程语言", "source": "python_intro.md"},
    ]
    # 给 chunks 配向量
    chunk_vecs = [_fake_vec(c["text"]) for c in chunks]
    retriever.add_chunks(chunks, chunk_vecs)
    print(f"  添加了 {retriever.count} 个 chunks")
    assert retriever.count == 3

    # 用第 0 个 chunk 的文本作为 query——应该能找到自己
    query_text = "Raft 选举领导者通过投票"
    results = retriever.search_by_text(query_text, top_k=2)
    print(f"  查询：'{query_text}'")
    print(f"  top-2 结果：")
    for r in results:
        print(f"    - [{r['chunk_id']}] {r['source']}: '{r['text'][:30]}...' score={r['score']:.4f}")

    assert len(results) >= 1
    assert results[0]["chunk_id"] == 0, "自查询应该命中自己"
    print("  [PASS] 自查询命中正确")


def _fake_vec(text: str) -> list:
    """生成确定性的 mock 向量（按文本长度 + 字符 ord 分布）。"""
    np.random.seed(hash(text) % (2**32))
    return np.random.randn(1024).astype(np.float32).tolist()


# ============ 测试 4：完整集成（需要 API key） ============
async def test_integration_real() -> None:
    """真实集成：文档 → 切片 → embed → 入库 → 检索。"""
    section("测试 4: 端到端集成（需要 API key）")
    if not has_siliconflow_key():
        print("  [SKIP] 未设置 SILICONFLOW_API_KEY，跳过真实集成测试")
        print("         设置后可验证：上传 Raft 文档，提问应能基于文档内容回答")
        return

    # 1. 加载真实文档
    text = (FIXTURES / "sample.md").read_text(encoding="utf-8")
    chunks = chunk_document(text, chunk_size=300, overlap=30)
    print(f"  文档切成 {len(chunks)} 段")

    # 2. Embed
    embeddings = SiliconFlowBGEEmbeddings(api_key=os.environ["SILICONFLOW_API_KEY"])
    vectors = await embeddings.aembed_documents([c["text"] for c in chunks])
    print(f"  embed 完成，得到 {len(vectors)} 个向量")

    # 3. 入库
    retriever = StudyCopilotRetriever(embedding=embeddings)
    for c in chunks:
        c["source"] = "sample.md"
    retriever.add_chunks(chunks, vectors)
    print(f"  入库 {retriever.count} 条")

    # 4. 检索
    question = "Raft 选举领导者怎么做？"
    results = retriever.search_by_text(question, top_k=2)
    print(f"  问题：'{question}'")
    for r in results:
        print(f"    - score={r['score']:.4f} [{r['source']}] {r['text'][:50]}...")

    assert len(results) >= 1
    print("  [PASS] 端到端集成成功")


async def main() -> int:
    try:
        await test_embedder_real()
        test_cosine_math()
        test_retriever_mock()
        await test_integration_real()
    except AssertionError as e:
        print(f"\n  [OVERALL FAIL] {e}")
        return 1

    section("全部测试通过！")
    print("  Phase 3 完成标志：")
    if has_siliconflow_key():
        print("    [x] 真实 SiliconFlow embed 跑通（1024 维向量）")
        print("    [x] 端到端集成：文档 → embed → 入库 → 检索 全部跑通")
    else:
        print("    [x] 真实 embed: SKIP（缺 SILICONFLOW_API_KEY）")
        print("    [x] numpy cosine 数学函数正确")
        print("    [x] Retriever 用 mock 向量跑通")
        print("    [ ] 端到端集成: 待设置 API key 后重跑")
    print("    [x] LangChain InMemoryVectorStore 集成正确")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
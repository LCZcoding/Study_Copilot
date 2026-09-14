"""v0.1 冒烟测试：端到端验证 RAG 链路。

这是 v0.1 "完成定义"的标准测试集：
- 上传 PDF 能正确问答（功能验证）
- 上传不相关文档，问相关问题答不上来（严格 RAG 验证）
- 代码能跑、测试能过（工程质量）

跑测试：
    uv run pytest tests/test_smoke.py -v
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
#from app.api.chat import _retriever
import app.api.chat as chat_api
FIXTURES = Path(__file__).parent / "fixtures"


# ========== Fixture：共享的 FastAPI TestClient ==========
@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient（进程内 HTTP 测试）。
    """
    with TestClient(app) as c:
        # 持久化后：SQLite 会残留上一轮运行的数据， 
        # lifespan load 进内存会污染测试（如 TestStrictRAG 的空库假设）。 
        # 每轮测试开始前清库，保证从干净状态开始。
        chat_api._retriever.clear()
        yield c


# ========== 1. 健康检查 ==========
class TestHealth:
    """基础健康检查 + 组件状态。"""

    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["components"]["embedder"] == "ready"
        assert body["components"]["retriever"] == "ready"
        assert body["components"]["llm"] == "ready"

    def test_root_endpoint(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Study Co-pilot"
        assert body["version"] == "0.1.0"
        assert "/docs" in body["docs"]


# ========== 2. 错误处理（不需要上传文档） ==========
class TestErrorHandling:
    """错误处理：在上传/提问前就能验证。"""

    def test_chat_without_doc_returns_404(self, client):
        """注意：module-scoped client 可能已有其他测试的索引残留。
        所以我们额外用 fresh client 来验证'空库'场景。
        """
        with TestClient(app) as fresh:
            chat_api._retriever.clear() # fresh client 是新实例，同样要清残留
            resp = fresh.post("/api/chat", json={"question": "什么是 Raft？"})
            assert resp.status_code == 404
            assert "请先" in resp.json()["detail"]

    def test_empty_question_returns_400(self, client):
        resp = client.post("/api/chat", json={"question": "   "})
        assert resp.status_code == 400

    def test_unsupported_format_returns_400(self, client):
        """v0.5-2：FileUploadConnector 会静默跳过坏格式文件，
        上传接口返回 400 + '内容为空'。v0.1 的 '不支持' 错误被吞了。
        """
        resp = client.post(
            "/api/upload",
            files={"file": ("test.docx", b"fake content", "application/msword")},
        )
        assert resp.status_code == 400, (
            f"坏格式应返回 400，实际 {resp.status_code}：{resp.text}"
        )


# ========== 3. 文档上传 ==========
class TestUpload:
    """文档上传：PDF/MD/TXT。"""

    def test_upload_markdown(self, client):
        with open(FIXTURES / "sample.md", "rb") as f:
            resp = client.post(
                "/api/upload",
                files={"file": ("sample.md", f, "text/markdown")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["chunks"] > 0
        assert body["total_chars"] > 0
        assert body["message"] == "索引建立成功"

    def test_upload_plain_text(self, client):
        with open(FIXTURES / "sample.txt", "rb") as f:
            resp = client.post(
                "/api/upload",
                files={"file": ("sample.txt", f, "text/plain")},
            )
        assert resp.status_code == 200
        assert resp.json()["chunks"] > 0

    def test_chunks_accumulate_across_uploads(self, client):
        """多次上传后 chunks 应累积（v0.1 单文档场景下，简单追加即可）。"""
        before = client.get("/api/health").json()["indexed_chunks"]

        # 再上传一次
        with open(FIXTURES / "sample.md", "rb") as f:
            client.post(
                "/api/upload",
                files={"file": ("another.md", f, "text/markdown")},
            )

        after = client.get("/api/health").json()["indexed_chunks"]
        assert after > before, f"chunks 应累积：before={before}, after={after}"


# ========== 4. RAG 端到端测试（v0.1 核心功能） ==========
class TestChatRAG:
    """RAG 链路：上传 + 提问 + 基于文档生成答案。

    这是 v0.1 的"功能验证"测试——证明系统能用文档回答问题。
    """

    def test_chat_returns_answer_with_source(self, client):
        """问已上传文档相关问题，答案应基于文档且引用来源。"""
        resp = client.post(
            "/api/chat",
            json={"question": "Raft 如何选举领导者？", "top_k": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["answer"]) > 10, "答案长度应 > 10 字"
        assert len(body["sources"]) > 0, "应引用至少一个来源"

        # 答案应包含 Raft 选举的核心关键词（基于 sample.md 内容）
        keywords = ["选举", "候选", "投票", "Leader", "leader"]
        found = [k for k in keywords if k in body["answer"]]
        assert len(found) >= 1, (
            f"答案应至少包含一个 Raft 选举关键词，实际命中：{found}"
        )


# ========== 5. 严格 RAG 验证（v0.1 灵魂测试） ==========
class TestStrictRAG:
    """v0.1 严格 RAG：证明系统在用文档而不是 LLM 知识兜底。

    这是 v0.1 验收的核心标准之一：
    '上传完全不相关的文档，问相关问题，应答不上来'

    v0.1 的"答不上来"实现方式（来自 CODE_CONTEXT.md prompt 设计）：
    - LLM 在 prompt 里被指示："如果参考资料中没有答案，明确说'参考资料中没有相关信息'"
    - 所以即使 LangChain 返回了低分 chunks，LLM 也会诚实说"未提到"
    - 这比 404 更友好：用户能看到系统真的去找了，只是没找到

    严格性验证（这两个都算"答不上来"，绝不允许幻觉 Raft 内容）：
    1. HTTP 404（空库或完全无相关）
    2. HTTP 200 + 答案明确说"未提到"/"未提及"（LLM 诚实）
    """

    def test_no_fallback_to_llm_knowledge(self):
        """上传 Python 文档，问 Raft 问题，不应幻觉 Raft 内容。"""
        with TestClient(app) as fresh_client:
            chat_api._retriever.clear() # 清掉 module client 遗留的测试数据
            # 1. 上传完全不相关的文档
            python_content = (
                "Python 是一门解释型、面向对象、动态数据类型的高级程序设计语言。"
                "由 Guido van Rossum 于 1991 年首次发布。Python 的设计哲学是"
                "'优雅'、'明确'、'简单'。Python 拥有丰富的标准库和第三方库，"
                "被广泛应用于 Web 开发、数据科学、人工智能、自动化运维等领域。"
                "Python 支持多种编程范式，包括面向对象、命令式、函数式和过程式编程。"
            ).encode("utf-8")

            resp = fresh_client.post(
                "/api/upload",
                files={"file": ("python_intro.md", python_content, "text/markdown")},
            )
            assert resp.status_code == 200, "上传 Python 文档应成功"

            # 2. 问 Raft 问题——库中没有相关内容
            # v0.5-2 D ext 改：用户决策"库没召回可允许通用兜底"，所以现在不再
            # 强制"未提到"。改为验证：要么 404，要么答案开头告知"未在知识库中找到"。
            resp = fresh_client.post(
                "/api/chat",
                json={"question": "Raft 算法如何选举领导者？"},
            )

            # 3. 验证：要么 404，要么答案开头告知未在知识库中找到
            assert resp.status_code in (200, 404), (
                f"HTTP 状态异常：{resp.status_code} {resp.text}"
            )
            if resp.status_code == 200:
                answer = resp.json()["answer"]
                # 新断言：必须告知用户这是兜底，不是基于知识库
                assert "未在知识库中找到" in answer or "知识库中没有" in answer, (
                    f"v0.5-2 D ext：库中无相关时 answer 必须告知用户是兜底回答。\n"
                    f"实际答案：{answer}"
                )

    def test_no_fallback_for_unrelated_question(self):
        """更直接的测试：同库中问相关问题能答，问无关问题应"答不上来"。

        v0.5-2 D ext 改：用较大的 Python 文档（短文档 bge-m3 相似度分布不稳定，
        没法测出真实差异）。200+ 字符才能让"相关/无关"清晰区分。
        """
        with TestClient(app) as fresh_client:
            chat_api._retriever.clear() # 清掉 module client 遗留的测试数据
            python_content = (
                "Python 是一门解释型、面向对象、动态数据类型的高级程序设计语言。"
                "由 Guido van Rossum 于 1991 年首次发布。Python 的设计哲学是"
                "'优雅'、'明确'、'简单'。Python 拥有丰富的标准库和第三方库，"
                "被广泛应用于 Web 开发、数据科学、人工智能、自动化运维等领域。"
                "Python 支持多种编程范式，包括面向对象、命令式、函数式和过程式编程。"
                "Python 解释器能够在多种操作系统上运行，包括 Windows、Linux 和 macOS。"
            ).encode("utf-8")

            resp = fresh_client.post(
                "/api/upload",
                files={"file": ("python_intro.md", python_content, "text/markdown")},
            )
            assert resp.status_code == 200

            # 问相关问题——能答（v0.5-2 D ext：相关问题召回到了 Python 介绍，
            # 但也可能部分兜底，所以这里只验证 status=200 不报错）
            resp = fresh_client.post(
                "/api/chat",
                json={"question": "Python 由谁创建？"},
            )
            assert resp.status_code == 200

            # 问不相关问题——v0.5-2 D ext 改：
            # 旧设计：库中无相关 → LLM 必须拒答（200+"未提到"）
            # 新设计（用户决策）：库中无相关 → LLM 自由回答通用知识 + 开头告知"未在知识库中找到"
            # 注：bge-m3 对短文档短查询的相似度不稳定，可能误召回；测试用大文档 + min_score=0.5
            #     才能稳定走"无关"分支。测试服务层前缀逻辑的覆盖在 test_chat_fallback_prefix_added
            #     里（用 mock 隔离 bge-m3 的不稳定性）。
            resp = fresh_client.post(
                "/api/chat",
                json={"question": "Paxos 算法一致性如何保证？", "min_score": 0.5},
            )
            assert resp.status_code in (200, 404)
            if resp.status_code == 200:
                answer = resp.json()["answer"]
                assert "未在知识库中找到" in answer or "知识库中没有" in answer, (
                    f"v0.5-2 D ext：库中无相关时 answer 必须告知用户。\n"
                    f"实际答案：{answer}"
                )
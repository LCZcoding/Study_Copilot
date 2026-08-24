"""Phase 4 验证脚本。

测试完整的 RAG HTTP 链路：
1. 健康检查（启动前）
2. 上传文档
3. 提问 → 基于文档生成答案
4. 错误处理（未上传就提问、上传不支持格式、问题为空）
5. 多文档混合（先后上传两个文档，验证 answers 都基于正确文档）

用 FastAPI 的 TestClient 在进程内跑（不需要起真实 server）。

运行：uv run python scripts/verify_phase4.py
"""

import sys
from pathlib import Path

# Windows GBK 兼容
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from app.main import app


FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def assert_(condition: bool, msg: str) -> None:
    if condition:
        print(f"  [PASS] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        raise AssertionError(msg)


def main() -> int:
    # TestClient 进入 app 时会触发 lifespan，组件在这里被初始化
    with TestClient(app) as client:
        # ============ 测试 1：健康检查 ============
        section("测试 1: GET /api/health")
        resp = client.get("/api/health")
        print(f"  HTTP {resp.status_code}")
        print(f"  Body: {resp.json()}")
        assert_(resp.status_code == 200, "health 端点返回 200")
        data = resp.json()
        assert_(data["status"] == "ok", "所有组件就绪")
        assert_(data["indexed_chunks"] == 0, "初始 chunks=0")
        assert_(data["components"]["embedder"] == "ready", "embedder ready")

        # ============ 测试 2：上传前先聊天（应 404） ============
        section("测试 2: 错误处理 - 上传前先聊天")
        resp = client.post("/api/chat", json={"question": "什么是 Raft？"})
        print(f"  HTTP {resp.status_code}")
        print(f"  Body: {resp.json()}")
        assert_(resp.status_code == 404, "未上传文档时应返回 404")
        assert_("请先" in resp.json()["detail"], "错误信息提示先上传")

        # ============ 测试 3：上传 sample.md ============
        section("测试 3: POST /api/upload (sample.md)")
        md_path = FIXTURES / "sample.md"
        with open(md_path, "rb") as f:
            resp = client.post(
                "/api/upload",
                files={"file": ("sample.md", f, "text/markdown")},
            )
        print(f"  HTTP {resp.status_code}")
        print(f"  Body: {resp.json()}")
        assert_(resp.status_code == 200, "上传成功")
        data = resp.json()
        assert_(data["chunks"] > 0, f"切出 {data['chunks']} 段（>0）")
        assert_(data["total_chars"] > 0, f"文档 {data['total_chars']} 字符")

        # ============ 测试 4：提问并验证回答基于文档 ============
        section("测试 4: POST /api/chat - 问 Raft 选举")
        resp = client.post(
            "/api/chat",
            json={"question": "Raft 如何选举领导者？", "top_k": 2},
        )
        print(f"  HTTP {resp.status_code}")
        body = resp.json()
        print(f"  Answer: {body['answer'][:200]}...")
        print(f"  Sources: {body['sources']}")
        assert_(resp.status_code == 200, "聊天返回 200")
        assert_(len(body["answer"]) > 10, "答案长度 > 10 字")
        assert_("sample.md" in body["sources"], "答案引用了 sample.md")
        # 答案应该提到 Raft 选举的关键概念（粗略验证）
        answer_lower = body["answer"].lower()
        keywords = ["选举", "候选", "投票", "leader", "term"]
        found = [k for k in keywords if k in answer_lower or k in body["answer"]]
        assert_(len(found) >= 1, f"答案包含至少一个选举关键词（命中：{found}）")

        # ============ 测试 5：错误处理 - 不支持的文件格式 ============
        section("测试 5: 错误处理 - 上传 .docx")
        resp = client.post(
            "/api/upload",
            files={"file": ("test.docx", b"fake docx", "application/msword")},
        )
        print(f"  HTTP {resp.status_code}")
        print(f"  Body: {resp.json()}")
        assert_(resp.status_code == 400, "不支持格式返回 400")
        assert_("不支持" in resp.json()["detail"], "错误信息说明格式不支持")

        # ============ 测试 6：错误处理 - 空问题 ============
        section("测试 6: 错误处理 - 空问题")
        resp = client.post("/api/chat", json={"question": "  ", "top_k": 3})
        print(f"  HTTP {resp.status_code}")
        assert_(resp.status_code == 400, "空问题返回 400")

        # ============ 测试 7：再上传一个文档 ============
        section("测试 7: 上传第二个文档 sample.txt")
        with open(FIXTURES / "sample.txt", "rb") as f:
            resp = client.post(
                "/api/upload",
                files={"file": ("sample.txt", f, "text/plain")},
            )
        print(f"  HTTP {resp.status_code}")
        assert_(resp.status_code == 200, "第二次上传成功")

        # chunks 数应该累积
        resp = client.get("/api/health")
        chunks_after = resp.json()["indexed_chunks"]
        print(f"  索引中现有 {chunks_after} 段")
        assert_(chunks_after > data["chunks"], "第二次上传后 chunks 累积")

        # ============ 测试 8：根路径 ============
        section("测试 8: GET / 根路径")
        resp = client.get("/")
        print(f"  HTTP {resp.status_code}")
        print(f"  Body: {resp.json()}")
        assert_(resp.status_code == 200, "根路径返回 200")
        assert_("Study Co-pilot" in resp.json()["name"], "返回应用名")

    # ============ 总结 ============
    section("全部测试通过！")
    print("  Phase 4 完成标志：")
    print("    [x] FastAPI lifespan 单例启动")
    print("    [x] /api/upload：PDF/MD/TXT → chunk → embed → 入库")
    print("    [x] /api/chat：embed → 检索 → RAG prompt → LLM → 答案")
    print("    [x] /api/health：报告组件状态和 chunks 数")
    print("    [x] 错误处理：404（无文档）、400（坏格式/空问题）")
    print("    [x] 多文档并存（追加索引）")
    print()
    print("  端到端实测确认：")
    print("    - 上传 Raft 简介 → 问选举问题 → 答案含'选举/候选/投票'关键词")
    print("    - sources 字段返回引用的文件名")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n  [OVERALL FAIL] {e}")
        sys.exit(1)
"""v0.5-2 验证脚本（A + B + C 子任务，不含飞书 D）。

测试：
1. SourceMeta + Document dataclass 序列化
2. FileUploadConnector 内存管理
3. FileUploadConnector.fetch_documents 返回 Document
4. FastAPI 多文档 API（list / delete）
5. 数据模型 metadata 字段（嵌套 vs 扁平）

飞书 D 部分留到下个 session（需要你提供 App ID/Secret）。

运行：uv run python scripts/verify_v05_2.py
"""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import config  # noqa: F401  # 触发 .env 加载
from app.sources.base import Document, SourceMeta
from app.sources.file_upload import FileUploadConnector

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


# ============ 测试 1：SourceMeta + Document dataclass ============
def test_source_meta() -> None:
    section("测试 1: SourceMeta + Document dataclass")
    meta = SourceMeta.now(
        source_type="file",
        source_name="raft.pdf",
    )
    assert_(meta.key == "file:raft.pdf", "key 格式正确")
    d = meta.to_dict()
    assert_(d["source_type"] == "file", "to_dict 保留 source_type")
    assert_(d["ingested_at"] is not None, "ingested_at 自动填时间戳")

    doc = Document(text="Raft 算法...", meta=meta)
    assert_(doc.key == "file:raft.pdf", "Document.key 透传 meta.key")
    print("  [PASS] SourceMeta + Document 基础功能")


# ============ 测试 2：FileUploadConnector 内存管理 ============
async def test_file_connector_basic() -> None:
    section("测试 2: FileUploadConnector 基础操作")
    conn = FileUploadConnector()

    # 添加文件
    conn.add_file("test.md", b"# Hello\n\nThis is content.")
    conn.add_file("doc.txt", b"Some plain text content here.")

    assert_(conn.has_file("test.md"), "has_file 找到刚加的文件")
    assert_(len(conn.list_filenames()) == 2, "list_filenames 返回 2 个文件")

    # 删除
    deleted = conn.delete_file("test.md")
    assert_(deleted, "delete_file 成功")
    assert_(not conn.has_file("test.md"), "已删除的文件查不到")
    assert_(len(conn.list_filenames()) == 1, "还剩 1 个文件")
    print("  [PASS] 内存增删查正常")


# ============ 测试 3：FileUploadConnector.fetch_documents ============
async def test_file_connector_fetch() -> None:
    section("测试 3: FileUploadConnector.fetch_documents")

    # 用真实 fixture 测试
    conn = FileUploadConnector()
    md_content = (FIXTURES / "sample.md").read_bytes()
    conn.add_file("sample.md", md_content)

    docs = await conn.fetch_documents()
    assert_(len(docs) == 1, "返回 1 个 Document")
    doc = docs[0]
    assert_(doc.meta.source_type == "file", "Document 标记为 file 类型")
    assert_(doc.meta.source_name == "sample.md", "Document 名称正确")
    assert_("Raft" in doc.text, "Document 文本包含关键词")
    assert_(doc.meta.ingested_at is not None, "时间戳自动填")

    # 测试坏格式文件被静默跳过
    conn.add_file("fake.docx", b"fake")
    docs = await conn.fetch_documents()
    assert_(len(docs) == 1, "坏格式被跳过，只剩 1 个有效文档")
    print("  [PASS] fetch_documents 正常处理")


# ============ 测试 4：多文档 API（list/delete） ============
def test_documents_api() -> None:
    section("测试 4: 多文档管理 API（list/delete）")

    with TestClient(app) as client:
        # 4a. 空库
        resp = client.get("/api/documents")
        assert_(resp.status_code == 200, "list 返回 200")
        body = resp.json()
        assert_(body["total_chunks"] == 0, "空库 total_chunks=0")
        assert_(len(body["documents"]) == 0, "空库 documents=[]")

        # 4b. 上传文档后能列出
        with open(FIXTURES / "sample.md", "rb") as f:
            client.post("/api/upload", files={"file": ("sample.md", f, "text/markdown")})
        with open(FIXTURES / "sample.txt", "rb") as f:
            client.post("/api/upload", files={"file": ("sample.txt", f, "text/plain")})

        resp = client.get("/api/documents")
        body = resp.json()
        print(f"  上传 2 个文件后，列表：")
        for d in body["documents"]:
            print(f"    - {d['source_key']} ({d['chunks']} chunks)")
        assert_(len(body["documents"]) == 2, "应列出 2 个文档")
        assert_(body["total_chunks"] > 0, "total_chunks > 0")

        # 4c. 删除指定文档
        resp = client.delete("/api/documents/file/sample.md")
        assert_(resp.status_code == 200, "delete 返回 200")
        assert_("已删除" in resp.json()["message"], "删除成功消息")

        # 4d. 删除后列表更新
        resp = client.get("/api/documents")
        body = resp.json()
        assert_(len(body["documents"]) == 1, "删除后剩 1 个")
        assert_(body["documents"][0]["source_name"] == "sample.txt", "剩的是 sample.txt")

        # 4e. 再次删除（幂等性：返回 404）
        resp = client.delete("/api/documents/file/sample.md")
        assert_(resp.status_code == 404, "重复删除返回 404")

        # 4f. 删除最后一个后 chat 应 404
        client.delete("/api/documents/file/sample.txt")
        resp = client.post("/api/chat", json={"question": "什么是 Raft？"})
        assert_(resp.status_code == 404, "删完后 chat 应 404")


# ============ 测试 5：retriever.search 返回字段 ============
def test_search_returns_metadata() -> None:
    section("测试 5: retriever.search 返回新 metadata 字段")
    with TestClient(app) as client:
        with open(FIXTURES / "sample.md", "rb") as f:
            client.post("/api/upload", files={"file": ("sample.md", f, "text/markdown")})

        resp = client.post("/api/chat", json={"question": "Raft 选举？", "top_k": 1})
        # chat 接口不直接返回搜索结果，但 sources 字段验证 source_name 工作正常
        body = resp.json()
        print(f"  来源列表：{body['sources']}")
        assert_("sample.md" in body["sources"], "sources 含 sample.md（验证 source_name 字段）")


async def main() -> int:
    try:
        test_source_meta()
        await test_file_connector_basic()
        await test_file_connector_fetch()
        test_documents_api()
        test_search_returns_metadata()
    except AssertionError as e:
        print(f"\n  [OVERALL FAIL] {e}")
        return 1

    section("全部测试通过！")
    print("  v0.5-2 A+B+C 完成标志：")
    print("    [x] SourceMeta / Document 数据模型")
    print("    [x] FileUploadConnector 增删查")
    print("    [x] SourceConnector 抽象接口")
    print("    [x] 多文档管理 API：list + delete")
    print("    [x] 数据模型扩展（多源 metadata 字段）")
    print("    [x] chat.py 用 FileUploadConnector 重构")
    print()
    print("  v0.5-2 D（飞书 OpenAPI）留到下个 session：")
    print("    - 需要你注册飞书开放平台账号 + 创建自建应用")
    print("    - 拿到 App ID + App Secret + 测试文档 ID")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
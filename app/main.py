"""FastAPI 应用入口。

v0.1 启动流程（lifespan）：
    1. 加载配置（app.core.config 在模块顶部 import 时已自动加载 .env）
    2. 实例化三个组件：embedder、retriever、llm
    3. 注册路由
    4. 监听 0.0.0.0:8000

Java 类比：相当于 Spring Boot 的 main() + ApplicationContext 初始化。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import init_components as init_chat_components
from app.api.chat import router as chat_router
from app.api.documents import init_components as init_documents_components
from app.api.documents import router as documents_router
from app.core.config import config
from app.core.llm.factory import create_router
from app.rag.embedder import SiliconFlowBGEEmbeddings
from app.rag.retriever import StudyCopilotRetriever
from app.sources.file_upload import FileUploadConnector


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan：启动时初始化组件，关闭时清理。"""
    # 启动阶段
    print("[startup] 初始化 RAG 组件...")

    # Embedder：调 SiliconFlow bge-m3
    sf_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not sf_key or sf_key == "your_siliconflow_api_key_here":
        raise RuntimeError(
            "SILICONFLOW_API_KEY 未设置，无法启动服务。"
            "请在 .env 中填入 API key"
        )
    embedder = SiliconFlowBGEEmbeddings(api_key=sf_key)

    # Retriever：内存向量存储
    retriever = StudyCopilotRetriever(embedding=embedder)

    # LLM Router：多 provider，按 priority 调度（v0.5-1 启用）
    # 当前启用：智谱（免费优先）+ qwen-turbo（付费兜底）
    llm = create_router(config.providers)

    # v0.5-2：文件上传数据源
    file_connector = FileUploadConnector()

    # 注入到各 router 的模块级单例
    init_chat_components(
        retriever=retriever, embedder=embedder, llm=llm, file_connector=file_connector
    )
    init_documents_components(retriever=retriever)

    print("[startup] 组件初始化完成")
    print(f"  - Embedder: bge-m3 (SiliconFlow)")
    # Router 显示所有启用的 provider
    provider_names = [f"{p.name}(p={p.priority})" for p in llm.providers]
    print(f"  - LLM Router: {provider_names}")
    print(f"  - Retriever: in-memory (LangChain InMemoryVectorStore)")
    print(f"  - FileUploadConnector: ready (v0.5-2)")

    # 应用运行（yield 把控制权交给 FastAPI）
    # lifespan 的核心是利用 Python 的异步上下文管理器 (asynccontextmanager) 和 yield 关键字。
    # yield 之前的代码在启动时执行，之后的代码在关闭时执行。
    yield 

    # 关闭阶段
    print("[shutdown] 清理资源...")
    # 当前都是内存组件，不需要显式清理
    # v0.5+ 接 PGVector / Redis 时在这里关闭连接


# 创建 FastAPI 应用
app = FastAPI(
    title="Study Co-pilot",
    description="个人 AI 学习助手 - 基于 RAG 的文档问答系统",
    version="0.1.0",
    lifespan=lifespan,
)

# 注册路由
app.include_router(chat_router, prefix="/api", tags=["chat"])
# v0.5-2：多文档管理路由
app.include_router(documents_router, prefix="/api", tags=["documents"])

# v0.5-3 PR1：允许 Vite dev server (localhost:5173) 跨域调 API。
# 生产环境部署到同源时这条不影响；分域部署时把生产域名也加进去。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """根路径：返回基本信息和文档链接。"""
    return {
        "name": "Study Co-pilot",
        "version": "0.1.0",
        "docs": "/docs",  # FastAPI 自动生成的 Swagger UI
        "endpoints": {
            "POST /api/upload": "上传文档（PDF/MD/TXT）",
            "POST /api/chat": "基于已上传文档问答",
            "GET /api/health": "健康检查",
            "GET /api/documents": "列出所有已索引文档（v0.5-2）",
            "DELETE /api/documents/{type}/{name}": "删除指定文档（v0.5-2）",
        },
    }


if __name__ == "__main__":
    # 开发时直接运行：python -m app.main
    # 生产用 uvicorn：uvicorn app.main:app --host 0.0.0.0 --port 8000
    import uvicorn

    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
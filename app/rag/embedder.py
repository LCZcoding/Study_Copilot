"""Embedding Provider：把文本转成向量。

设计选择：用 LangChain 标准 `OpenAIEmbeddings` + 自定义 base_url 指向 SiliconFlow。
LangChain 帮我们挡住的"低层细节"：
- HTTP 请求构造、鉴权头（Bearer token）
- 自动分批（按 chunk size 切批，避免单次请求过大）
- 重试与错误处理（429/超时自动重试）
- 同步/异步双版本（OpenAIEmbeddings 自带 aembed_* 包装）
- token 计数、usage 统计

我们要做的只是配置三件事：
1. api_key（鉴权）
2. base_url（指向 SiliconFlow，因为它的 API 兼容 OpenAI）
3. model（指定 bge-m3）

为什么 SiliconFlow 兼容 OpenAI？
- SiliconFlow 是国内 LLM API 聚合平台，复用 OpenAI 的请求/响应格式
- 所以 LangChain 的 OpenAIEmbeddings 加上自定义 base_url 就能直接对接
- 这是国内 API 厂商的常见做法（DeepSeek / 智谱 / 月之暗面等都兼容 OpenAI）

为什么不自己写 httpx 直调？
- 重复造轮子，且 LangChain 已经把 HTTP/重试/分批/异步都做好了
- 如果未来要切 DeepSeek / OpenAI 官方 / 其他 OpenAI 兼容服务，只改 base_url + model
"""

from langchain_openai import OpenAIEmbeddings


# 模块级常量：默认配置
# 注意：必须放类外——OpenAIEmbeddings 是 Pydantic 模型，子类的 self.* 在 __init__ body
# 执行时还不可用（Pydantic 字段初始化早于 __init__ body），所以类属性读不到。
DEFAULT_MODEL = "BAAI/bge-m3"  # SiliconFlow 的 bge-m3 模型标识
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1/"
EMBEDDING_DIM = 1024  # bge-m3 输出维度，整个 retriever 都依赖这个数字


class SiliconFlowBGEEmbeddings(OpenAIEmbeddings):
    """SiliconFlow 的 bge-m3 Embedding，基于 LangChain OpenAIEmbeddings。

    继承而非包装（composition）的原因：
    - OpenAIEmbeddings 本身就是 LangChain 的 `Embeddings` 子类
    - 我们只改默认参数（api_url、model），保留所有 LangChain 已实现的功能
    - Java 类比：相当于子类化 `RestTemplate` 设默认 baseUrl，省去每次构造时传参
    """

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ):
        # 把 SiliconFlow 的默认配置透传给父类 OpenAIEmbeddings。
        # 父类会处理：HTTP 客户端初始化、并发数设置（chunk_size）、重试参数等。
        super().__init__(
            api_key=api_key,
            base_url=base_url or DEFAULT_BASE_URL,
            model=model or DEFAULT_MODEL,
            # chunk_size 控制 LangChain 内部自动分批大小。
            # bge-m3 单批上限 32（SiliconFlow 服务端硬限制），这里设 16 留余量。
            chunk_size=16,
            # 请求超时（秒）
            timeout=30,
            # 最大重试次数（429 / 5xx 会自动重试）
            max_retries=2,
            **kwargs,
        )

    # ========== 父类已实现的能力（注释展示 LangChain 在背后做了什么） ==========
    #
    # 1. embed_documents(texts: List[str]) -> List[List[float]]
    #    LangChain 内部：
    #    - 按 chunk_size 自动分批（我们设了 16，所以 50 个文本会自动切成 4 批）
    #    - 每批调一次 OpenAI /embeddings 接口
    #    - 自动处理 429 限流、5xx 服务端错误的指数退避重试
    #    - 拼接所有批次的返回结果
    #
    # 2. embed_query(text: str) -> List[float]
    #    LangChain 内部：直接调 embed_documents([text])，再取 [0]
    #    （bge 系列早期版本对"查询"用不同前缀，所以 LangChain 保留这个区分）
    #
    # 3. aembed_documents / aembed_query（async 版本）
    #    LangChain 内部：用 asyncio 包装 sync 版本（httpx async client）
    #    业务代码用 await 即可，不用关心是 sync 还是 async
    #
    # 4. check_health（我们额外加的）
    #    LangChain 的 OpenAIEmbeddings 没有自带 health check，
    #    所以这里手写一个最小测试：embed 一个短文本，能成功就说明服务可用。
    #    这跟 Phase 1 中 ZhipuProvider.check_health() 的思路一致。

    def check_health(self) -> bool:
        """健康检查：embed 一个最小文本，能成功就说明 SiliconFlow 服务可用。"""
        try:
            self.embed_query("ping")
            return True
        except Exception:
            return False
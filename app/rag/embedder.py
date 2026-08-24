"""Embedding Provider：调 SiliconFlow 的 bge-m3 模型把文本转成向量。

设计选择：用 LangChain 标准 `Embeddings` 接口 + httpx 直调 SiliconFlow API。
- 不用 LangChain 的 `OpenAIEmbeddings` 间接层，是因为 SiliconFlow API 简单到直接调更清晰
- 用 LangChain 接口是为了将来能直接接入 `InMemoryVectorStore`（Phase 3 主用）

为什么 bge-m3？
- 中文效果好（BAAI 智源研究院出品）
- 1024 维向量，对个人项目够用
- SiliconFlow 提供 API + 200M tokens 免费额度，新用户能跑一年
"""

from typing import List

import httpx
from langchain_core.embeddings import Embeddings


class SiliconFlowBGEEmbeddings(Embeddings):
    """LangChain 标准 Embeddings 实现，调 SiliconFlow 的 bge-m3 模型。

    继承 LangChain 的 `Embeddings` 抽象类——这是 LangChain 生态的"行业标准接口"，
    所有 LangChain 向量存储（FAISS、Chroma、PGVector 等）都接受任何实现了这个接口的对象。
    换 Embedding 模型时只需换这一个类，业务代码完全不用动。
    """

    # 类属性：模型标识。bge-m3 输出 1024 维向量，这个数字会贯穿整个 retriever。
    model: str = "BAAI/bge-m3"
    # SiliconFlow API 端点。OpenAI 兼容格式，所以路径是 /v1/embeddings。
    api_url: str = "https://api.siliconflow.cn/v1/embeddings"
    # 单次请求最多 32 个文本。bge-m3 的硬限制，由 SiliconFlow 服务端校验。
    max_batch_size: int = 32

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        api_url: str | None = None,
    ):
        # LangChain 的 Embeddings 是 sync 接口（embed_documents / embed_query），
        # 但 LangChain 0.3+ 会自动生成对应的 async 版本（aembed_documents），
        # 它们内部用 asyncio.to_thread 包装 sync 方法。所以我们这里只写 sync 版本即可。
        self.api_key = api_key
        if model:
            self.model = model
        if api_url:
            self.api_url = api_url

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        """实际调 SiliconFlow API，把一段文本列表转成向量列表。

        这是核心实现。LangChain 接口的两个方法（embed_documents / embed_query）
        都会走到这里。
        """
        if not texts:
            return []

        # SiliconFlow API 用 Bearer token 鉴权，格式："Authorization: Bearer <api_key>"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 请求体：OpenAI 兼容格式
        payload = {
            "model": self.model,
            "input": texts,
            # "encoding_format": "float" 是默认值（也可以是 "base64"），这里显式写出来更清楚
            "encoding_format": "float",
        }

        # 同步 HTTP 调用。httpx 默认超时 5s 太短，AI API 通常需要更长，设为 30s。
        # Java 类比：类似 RestTemplate.postForObject()，但更现代（自动 JSON 序列化）。
        with httpx.Client(timeout=30.0) as client:
            response = client.post(self.api_url, json=payload, headers=headers)

        # 4xx/5xx 不会自动抛异常，要手动 raise。错误信息尽量保留原始 body 方便排查。
        if response.status_code != 200:
            raise RuntimeError(
                f"SiliconFlow API 调用失败：HTTP {response.status_code} - {response.text}"
            )

        # 响应格式：{"data": [{"embedding": [...1024 floats...], "index": 0}, ...], ...}
        data = response.json()["data"]
        # 按 index 排序（虽然一般 API 返回就是有序的，但保险起见）
        data_sorted = sorted(data, key=lambda x: x["index"])
        return [item["embedding"] for item in data_sorted]

    def _split_batches(self, texts: List[str]) -> List[List[str]]:
        """把长列表切成 ≤32 个一组的小批次。

        为什么需要？bge-m3 单次请求最多 32 个文本（SiliconFlow 服务端硬限制），
        超过会报 400。所以客户端要先分批。
        """
        return [
            texts[i : i + self.max_batch_size]
            for i in range(0, len(texts), self.max_batch_size)
        ]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """LangChain 标准接口：批量 embed 文档。

        返回 List[List[float]]：外层每个元素对应一段文本，内层是该文本的 1024 维向量。
        多个批次的结果会拼接成一个完整的列表返回。

        Java 类比：类似一个 mapper.map(texts) 的批量处理方法。
        """
        if not texts:
            return []

        batches = self._split_batches(texts)
        all_vectors: List[List[float]] = []
        for batch in batches:
            vectors = self._call_api(batch)
            all_vectors.extend(vectors)

        return all_vectors

    def embed_query(self, text: str) -> List[float]:
        """LangChain 标准接口：embed 单个查询。

        为什么要和 embed_documents 分开？因为有些 Embedding 模型对"文档"和"查询"
        用不同的前缀（比如 bge 系列早期版本），分开调用便于将来定制。
        现在 bge-m3 不区分，所以两个方法最终都走 _call_api。
        """
        # 直接复用 _call_api，不要 embed_documents([text])——避免一次额外的列表包装。
        return self._call_api([text])[0]

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """LangChain 自动生成的 async 版本（这里我们重写以真正支持 async）。

        LangChain 0.3 默认会用 asyncio.to_thread 包 sync 版本，
        但 httpx 支持原生 async，重写可以获得真正的异步性能。
        """
        if not texts:
            return []

        batches = self._split_batches(texts)
        all_vectors: List[List[float]] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            for batch in batches:
                payload = {
                    "model": self.model,
                    "input": batch,
                    "encoding_format": "float",
                }
                response = await client.post(self.api_url, json=payload, headers=headers)
                if response.status_code != 200:
                    raise RuntimeError(
                        f"SiliconFlow API 失败：HTTP {response.status_code} - {response.text}"
                    )
                data = response.json()["data"]
                data_sorted = sorted(data, key=lambda x: x["index"])
                all_vectors.extend(item["embedding"] for item in data_sorted)

        return all_vectors

    async def aembed_query(self, text: str) -> List[float]:
        """LangChain 标准接口的 async 版本：embed 单个查询。"""
        result = await self.aembed_documents([text])
        return result[0]

    def check_health(self) -> bool:
        """健康检查：embed 一个短文本，能成功就说明服务可用。

        这是 Phase 1 抽象接口的扩展（虽然现在不再继承 EmbeddingProvider），
        但保留这个方法供上层 Router / 监控用。
        """
        try:
            self._call_api(["health check"])
            return True
        except Exception:
            return False
"""智谱 GLM-4-Flash 实现。

GLM-4-Flash 是智谱 AI 提供的完全免费的 LLM，中文能力强，速度快，覆盖日常 80% 场景。
访问 https://bigmodel.cn/ 注册并获取 API Key。

SDK 说明：`zhipuai` 是智谱官方 Python SDK（类似 Java 的官方 Java SDK），
封装了 HTTP 请求、鉴权（JWT）、消息格式转换，我们只调高层 API。
"""

import time
from typing import AsyncGenerator, List

# zhipuai 是智谱官方 SDK，封装了 GLM 系列模型的调用。
# 同步版本 ZhipuAI() 内部其实是同步阻塞调用，我们手动包一层让它看起来像异步。
from zhipuai import ZhipuAI

from .base import ChatMessage, ChatResponse, LLMProvider


class ZhipuProvider(LLMProvider):
    """智谱 GLM-4-Flash 实现。

    v0.1 唯一的真实 provider。所有对话和生成都走这里。

    Java 类比：相当于 Spring 里 `@Service` 标注的实现类。
    """

    name = "zhipu"

    def __init__(self, api_key: str, model: str = "glm-4-flash"):
        # ZhipuAI 是 SDK 的客户端对象，类似 OkHttpClient / RestTemplate。
        # 创建一次复用，不要每次调用都 new（会重复建连接池）。
        self.client = ZhipuAI(api_key=api_key)
        self.model = model

    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        """同步对话：等待完整响应返回。

        Java 类比：类似调 `httpClient.send(request, BodyHandlers.ofString())` 等响应。
        整个调用其实是同步阻塞的，但包了 async 关键字后可以 await（跟 FastAPI 异步框架兼容）。
        """
        start = time.time()

        # Pydantic 模型的 .model_dump() 类似 Java record 的 accessor，把对象转成 dict。
        # 智谱 SDK 要求传入 OpenAI 兼容格式：[{"role": "...", "content": "..."}]
        msg_dicts = [m.model_dump() for m in messages]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=msg_dicts,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 注意：智谱 SDK 的 chat.completions.create 是同步方法，
        # 我们没真正异步（避免引入 asyncio.to_thread 增加复杂度），
        # 但 async 关键字让上层 await 调用方语法统一，业务代码不用分同步/异步两套。
        latency_ms = int((time.time() - start) * 1000)

        choice = response.choices[0]
        # usage 可能为 None（极端情况），用 or {} 兜底。
        usage = response.usage or {}

        return ChatResponse(
            content=choice.message.content,
            model=self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            # GLM-4-Flash 完全免费，所以成本永远是 0。
            cost_cny=0.0,
            latency_ms=latency_ms,
        )

    async def stream_chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncGenerator[str, None]:
        """流式对话：每生成一段就 yield 一段。

        用法：
            async for chunk in provider.stream_chat(messages):
                print(chunk, end="", flush=True)

        Java 类比：返回 SSE（Server-Sent Events）的客户端流，类似 WebClient 的 bodyToFlux。
        """
        msg_dicts = [m.model_dump() for m in messages]
        response = self.client.chat.completions.create(
            model=self.model,
            messages=msg_dicts,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,  # 关键参数：开启流式
        )
        for chunk in response:
            # 流式响应每个 chunk 只有部分内容；delta.content 可能为 None（首个 chunk）。
            delta = chunk.choices[0].delta
            if delta and delta.content:
                # yield 是 Python 生成器的关键字，类似 Java 的 Flux.create(sink -> sink.next())。
                yield delta.content

    async def check_health(self) -> bool:
        """健康检查：发个最小请求"ping"，能成功就说明服务可用。

        这是经典的"主动健康检查"模式，比单纯 ping HTTP 端点更靠谱（能验证鉴权和完整链路）。
        """
        try:
            await self.chat(
                [ChatMessage(role="user", content="ping")],
                temperature=0.0,
                max_tokens=10,
            )
            return True
        except Exception:
            # 实际项目应记录日志，方便排查（v1.0+ 加 logger）。
            return False

    def estimate_cost(self, messages: List[ChatMessage]) -> float:
        """预估成本。GLM-4-Flash 完全免费，永远返回 0。

        即使是预估，这个方法也要存在——Router 调度时需要按 cost 排序。
        后续加付费 provider 时，这个方法里就要算真实的 ¥了。
        """
        return 0.0
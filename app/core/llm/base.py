"""LLM Provider 抽象层。

设计目标：所有 LLM 供应商（智谱、通义千问、DeepSeek 等）实现同一套接口，
新增 provider 只需写一个实现类 + 在 config/llm.yaml 里加配置，不改业务代码。

Java 类比：这就是 Java 里定义 `interface LLMProvider` + 一堆实现类的做法，
只不过 Python 用 ABC（Abstract Base Class）来强制子类必须实现所有抽象方法。
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List

# Pydantic 是 Python 的数据校验库，类似 Java 的 Lombok + JSR-303（@NotNull 之类）。
# 用 BaseModel 定义数据类，自动获得：类型检查、JSON 序列化、字段校验。
from pydantic import BaseModel, Field


class ChatMessage(BaseModel): # 继承BaseModel
    """单条对话消息。

    Java 类比：类似一个简单的 DTO/Record，包含 role 和 content 两个字段。
    role 取值约定："system"（系统提示，设 LLM 行为）|"user"（用户）|"assistant"（AI 回复）。
    """

    # ...在pydantic表示必填
    # 用于提供字段的文本描述。它不会改变代码的运行逻辑，但会被 pydantic 提取出来，
    # 用于生成 JSON Schema、API 文档（如 Swagger UI），
    # 或者在构建大语言模型（LLM）的 Function Calling 提示词时，帮助模型理解该字段的含义。
    role: str = Field(..., description="消息角色：system | user | assistant")
    content: str = Field(..., description="消息文本内容")


class ChatResponse(BaseModel):
    """LLM 调用的标准响应。

    包含内容、模型名、token 用量、成本、耗时。所有 provider 都返回这个格式，
    上层业务代码不需要知道用的是哪家供应商。
    """

    content: str = Field(..., description="LLLM 生成的回复内容")
    model: str = Field(..., description="实际使用的模型名")
    input_tokens: int = Field(0, description="输入消耗的 tokens") # 0表示默认值
    output_tokens: int = Field(0, description="输出消耗的 tokens")
    cost_cny: float = Field(0.0, description="本次调用花费（人民币元）")
    latency_ms: int = Field(0, description="本次调用耗时（毫秒）")


class LLMProvider(ABC):
    """所有 LLM 供应商的抽象基类。

    Java 类比：相当于 `public abstract class LLMProvider`，子类必须实现所有 @abstractmethod。
    如果哪天想换成 DeepSeek，写个 DeepSeekProvider 继承这个类即可，业务代码一行不用改。
    """

    # 类属性：provider 的标识名。子类应覆盖它，如 "zhipu"、"qwen_turbo"。
    name: str = "abstract"
    # 调度优先级：数字越小优先级越高。Router 用它排序。
    # v0.5 默认：智谱=1（免费优先）、qwen_turbo=2（付费兜底）。
    priority: int = 1

    @abstractmethod # 必须实现的抽象方法
    async def chat( # 异步，提前返回结果
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        """同步对话：发完请求等完整结果返回。

        Java 类比：async def 类似 Java 的 CompletableFuture<ChatResponse>，
        调用方用 await 等待结果。Python 异步是事件循环驱动的，跟 Java 线程池思路不同。
        """
        raise NotImplementedError # 占位符，子类不实现就报错

    @abstractmethod
    def stream_chat(
        self, # 表示 this
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncGenerator[str, None]: # AsyncGenerator异步生成器对象，参数1：产出的数据类型、参数2：用户能不能输入信息
        """流式对话：边生成边返回（打字机效果）。

        返回类型 AsyncGenerator[str, None] 表示"异步生成器，每次产出一个字符串片段"。
        Java 没有原生对应概念，最接近的是 Reactive Streams 的 Flux<String>。
        """
        raise NotImplementedError
        # 上面这行让函数变成生成器（虽然永远不执行）。Python 的语法特性。

    @abstractmethod
    async def check_health(self) -> bool:
        """健康检查：调用一次轻量请求，确认 provider 可用。

        返回 True 表示 provider 能正常响应。Router 调度前会调这个判断是否跳过它。
        """
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, messages: List[ChatMessage]) -> float:
        """预估成本（人民币元）。

        用于调度决策：免费 provider 优先于付费 provider；贵模型优先于便宜模型。
        注意：是"预估"——基于输入消息长度估算，不包含输出长度（输出在 chat 后才知道）。
        """
        raise NotImplementedError


class EmbeddingProvider(ABC):
    """Embedding 模型供应商抽象。

    Embedding 是把文本转成向量的过程，相似的文本向量距离近。
    用于 RAG 检索：把文档和问题都转成向量，找最相似的几个文档喂给 LLM。

    v0.1 阶段不直接使用（Phase 3 才用），但接口先定义好避免后续重构。
    """

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """把一段文本列表转成向量列表。

        返回 List[List[float]]：外层每个元素对应一段文本，内层是该文本的向量。
        bge-m3 模型输出 1024 维，所以内层 list 长度固定 1024。
        """
        raise NotImplementedError

    @abstractmethod
    async def check_health(self) -> bool:
        """Embedding 服务的健康检查。"""
        raise NotImplementedError
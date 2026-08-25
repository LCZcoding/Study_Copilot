"""LLM Router：多 provider 调度器。

v0.5 引入：失败 fallback 机制。
- 按 priority 排序所有 provider
- 依次调用，失败（异常）切下一个
- 全部失败抛 AllProvidersFailedError

v0.5 简化：
- 不做余额检测（v0.5-4 一起做）
- 不做熔断器（v1.0+）
- 不做效果评分（v1.0+）

Java 类比：类似 Spring Retry 的 RetryTemplate，但粒度更细（按 provider 切）。
"""

from typing import List

from .base import ChatMessage, ChatResponse, LLMProvider


class AllProvidersFailedError(Exception):
    """所有 provider 都失败时抛此异常。"""


class LLMRouter:
    """多 provider 路由器。

    用法：
        router = LLMRouter([zhipu_provider, qwen_provider])
        response = await router.chat(messages)

    行为：
        - 按 priority 升序排序（priority 数字小的先试）
        - 遇到异常时记录日志，切下一个 provider
        - 所有 provider 都失败 → AllProvidersFailedError
    """

    def __init__(self, providers: List[LLMProvider]):
        if not providers:
            raise ValueError("至少需要一个 provider")
        # 按 priority 升序排序：priority 小的先试
        self.providers = sorted(providers, key=lambda p: p.priority) #根据每个provider的priority字段增排序。lambda类似函数引用（匿名函数）
        self._history: List[dict] = []  # 记录每次调用的 provider 和结果

    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        """按优先级依次尝试，成功立刻返回，全部失败抛异常。

        关键设计：catch 所有异常 → 切下一个。这是 v0.5 的简化策略。
        v0.5-4 会引入更细的错误分类（不可恢复错误如 Prompt 错误应直接抛）。
        """
        last_error: Exception | None = None #Exception | None 是类型注解

        for provider in self.providers:
            try:
                response = await provider.chat(
                    messages, temperature=temperature, max_tokens=max_tokens
                )
                # 成功：记录并返回
                self._record_success(provider.name)
                return response
            except Exception as e:
                # 失败：记录并切下一个
                self._record_failure(provider.name, str(e))
                last_error = e
                continue

        # 所有 provider 都失败
        raise AllProvidersFailedError(
            f"所有 {len(self.providers)} 个 provider 都失败，最后错误：{last_error}"
        )

    def estimate_cost(self, messages: List[ChatMessage]) -> float:
        """预估成本：返回最便宜 provider 的成本。

        v0.5 简化：只用于"哪个便宜选哪个"的调度决策。
        实际计费以 chat() 返回的 cost_cny 为准。
        """
        costs = [(p.estimate_cost(messages), p.name) for p in self.providers] #构建成本，providername元组
        return min(costs)[0] # 元组中的第一个元素是成本，第二个是name

    def _record_success(self, provider_name: str) -> None:
        """记录调用历史（供调试和未来统计）。"""
        self._history.append({"provider": provider_name, "result": "success"})

    def _record_failure(self, provider_name: str, error: str) -> None:
        """记录失败（供调试和未来熔断逻辑使用）。"""
        self._history.append(
            {"provider": provider_name, "result": "failure", "error": error}
        )

    @property #把一个方法伪装成属性、调用更加优雅
    def history(self) -> List[dict]:
        """调用历史（最近 N 次），方便调试。"""
        return self._history[-20:]  # 只保留最近 20 条

    async def check_health(self) -> bool:
        """健康检查：第一个 provider 能通就行（按优先级）。"""
        for provider in self.providers:
            if await provider.check_health():
                return True
        return False
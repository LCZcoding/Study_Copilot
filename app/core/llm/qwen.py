"""通义千问 qwen-turbo 实现（阿里云百炼平台）。

阿里云百炼（DashScope）API 与 OpenAI 兼容，所以实现模式跟 ZhipuProvider 几乎一样：
都是封装 HTTP 调用 + 鉴权 + 消息格式。

为什么用 qwen-turbo 作为兜底？
- 速度快、价格低（¥0.3/¥0.6 每百万 tokens）
- 中文能力强
- 跟智谱形成"国内双供应商"，单点故障风险低
- 智谱完全免费所以优先用；qwen-turbo 付费只在智谱失败时启用

访问：https://bailian.console.aliyun.com/ 注册并获取 API Key。
"""

import time
from typing import AsyncGenerator, List

import httpx

from .base import ChatMessage, ChatResponse, LLMProvider


class QwenTurboProvider(LLMProvider):
    """通义千问 qwen-turbo 实现。

    v0.5+ 启用：用 OpenAI 兼容 API 调阿里云百炼。
    跟 ZhipuProvider 的代码结构几乎一样——因为两家都是 OpenAI 兼容 API。
    """

    name = "qwen_turbo"
    # 兜底 provider：智谱失败才用，所以优先级低
    priority = 2

    def __init__(self, api_key: str, model: str = "qwen-turbo"):
        # 阿里云百炼的 OpenAI 兼容端点
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.api_key = api_key
        self.model = model

    def _call_api(self, messages: List[dict], temperature: float, max_tokens: int, stream: bool = False):
        """实际调 OpenAI 兼容 API（同步版，给 chat 和 stream_chat 共用）。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        with httpx.Client(timeout=30.0) as client:# with：用完自动关掉client
            response = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"Qwen API 调用失败：HTTP {response.status_code} - {response.text}"
            )
        return response

    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        """同步对话：等待完整响应。"""
        start = time.time()
        msg_dicts = [m.model_dump() for m in messages]
        response = self._call_api(msg_dicts, temperature, max_tokens, stream=False)
        latency_ms = int((time.time() - start) * 1000)

        body = response.json()
        choice = body["choices"][0]
        # qwen-turbo 的 usage 字段：prompt_tokens / completion_tokens / total_tokens
        usage = body.get("usage", {})

        return ChatResponse(
            content=choice["message"]["content"],
            model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            # qwen-turbo 价格：¥0.3/¥0.6 每百万 tokens（输入/输出）
            # 单次成本 = input_tokens / 1_000_000 * 0.3 + output_tokens / 1_000_000 * 0.6
            cost_cny=(
                usage.get("prompt_tokens", 0) / 1_000_000 * 0.3
                + usage.get("completion_tokens", 0) / 1_000_000 * 0.6
            ),
            latency_ms=latency_ms,
        )

    async def stream_chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncGenerator[str, None]:
        """流式对话：每生成一段就 yield 一段。"""
        msg_dicts = [m.model_dump() for m in messages]
        response = self._call_api(msg_dicts, temperature, max_tokens, stream=True)

        # ========== SSE (Server-Sent Events) 解析详解 ==========
        #
        # 什么是 SSE：服务器推送事件的 W3C 标准协议，OpenAI/百炼等流式 API 都用它。
        # 数据通过 HTTP 长连接持续推送，客户端逐行解析。
        #
        # 实际数据长这样（百炼/Qwen 的 OpenAI 兼容流式响应）：
        #   data: {"choices":[{"delta":{"content":"你"}}]}
        #   data: {"choices":[{"delta":{"content":"好"}}]}
        #   ...
        #   data: [DONE]
        #   \n\n   ← 每个 data 行之间用一个空行分隔（HTTP 帧边界）
        #
        # 关键规则：
        # 1. 每行以 "data: " 开头（注意冒号后有个空格）
        # 2. 最后一行是 "data: [DONE]" 表示流结束
        # 3. 至少有3种行需要跳过：
        #    - 空行（帧分隔符 "\n\n"）
        #    - 注释行（OpenAI 偶尔发 ": OPENAI-STREAMING" 这种）
        #    - 开头没 "data: " 的心跳行（keep-alive）
        #
        # 为什么用 iter_lines() 而不是 response.text：
        # - iter_lines() 按 \n 切分但不会把多行数据合并
        # - response.text 会一次性返回全部内容，内存爆炸
        # - SSE 流式可能要持续几分钟，必须逐行处理

        for line in response.iter_lines():
            # 三种情况跳过这行：
            # 1. 空行（None 或 ""）→ 帧分隔符
            # 2. 不是以 "data: " 开头 → 注释行或心跳
            if not line or not line.startswith("data: "):
                continue

            # 去掉 "data: " 前缀（6 个字符："d", "a", "t", "a", ":", " "）
            # 为什么要切片 [6:] 而不是用 replace("data: ", "")：
            # - 切片是 O(1) 操作，replace 至少扫一遍字符串
            # - 切片不依赖"data: "只出现一次"（replace 会替换所有出现位置）
            data_str = line[6:]

            # SSE 流结束标志：服务端发完所有内容后会发 "data: [DONE]"
            # .strip() 是为了防止 "[DONE]" 前后有意外空白
            if data_str.strip() == "[DONE]":
                break

            # 把 JSON 字符串解析成 Python dict
            # import 放在这里是延迟导入（仅在真正需要解析时才导入 json 模块）
            # - 流式响应可能很快结束，json 是 stdlib 不影响启动速度
            # - 避免在文件顶部 import 增加冷启动开销
            import json
            chunk = json.loads(data_str)

            # SSE 的每个 chunk 只包含增量内容（"你" 然后 "好"），不是完整回复
            # .get("delta", {}) 防御性写法：万一某个 chunk 没有 delta 字段不报错
            delta = chunk["choices"][0].get("delta", {})
            # 只有当本 chunk 有 content 时才 yield（有些 chunk 只有 role 信息）
            if delta.get("content"):
                yield delta["content"]

    async def check_health(self) -> bool:
        """健康检查：发个最小请求，能成功就说明服务可用。"""
        try:
            await self.chat(
                [ChatMessage(role="user", content="ping")],
                temperature=0.0,
                max_tokens=10,
            )
            return True
        except Exception:
            return False

    def estimate_cost(self, messages: List[ChatMessage]) -> float:
        """预估成本（输入部分，输出未知）。"""
        # 粗略估算：按字符数 / 2 算 token 数（中英文混合的经验值）
        # 真实场景应该用 tokenizer 精确算
        total_chars = sum(len(m.content) for m in messages)
        estimated_tokens = total_chars / 2
        return estimated_tokens / 1_000_000 * 0.3  # 输入价格 ¥0.3/M tokens
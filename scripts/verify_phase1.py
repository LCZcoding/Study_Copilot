"""Phase 1 验证脚本。

逐项验证抽象接口、ZhipuProvider 实现、配置加载：
1. config 加载 + 环境变量替换
2. ZhipuProvider.chat 同步调用
3. ZhipuProvider.stream_chat 流式调用
4. ZhipuProvider.check_health
5. ZhipuProvider.estimate_cost
6. ZhipuProvider 实现了 LLMProvider 所有抽象方法（实例化不报错）

运行：uv run python scripts/verify_phase1.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Windows 默认 GBK，强制 UTF-8 输出 emoji
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from app.core.config import load_config
from app.core.llm.zhipu import ZhipuProvider
from app.core.llm.base import ChatMessage, LLMProvider


def section(title: str) -> None:
    """打印一个分隔的小节标题。"""
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def test_config_loading() -> None:
    """测试 1: YAML 配置能加载，且 zhipu provider 已启用。"""
    section("测试 1: 配置加载")
    config = load_config()
    print(f"  加载到 {len(config.providers)} 个 provider")
    for p in config.providers:
        print(f"    - {p.name} (enabled={p.enabled}, impl={p.impl})")
    zhipu = next((p for p in config.providers if p.name == "zhipu"), None)
    assert zhipu is not None, "zhipu provider 不在配置中"
    assert zhipu.enabled, "zhipu provider 未启用"
    print("  [PASS] zhipu provider 已正确配置")


async def test_provider_basic(provider: ZhipuProvider) -> None:
    """测试 2-6: ZhipuProvider 的所有方法。"""
    section("测试 2: estimate_cost()")
    cost = provider.estimate_cost(
        [ChatMessage(role="user", content="随便问点什么")]
    )
    print(f"  预估成本：¥{cost}")
    assert cost == 0.0, "GLM-4-Flash 应该是免费的"
    print("  [PASS] 成本为 0")

    section("测试 3: check_health()")
    healthy = await provider.check_health()
    print(f"  健康状态：{healthy}")
    assert healthy, "check_health() 应该返回 True"
    print("  [PASS] provider 健康")

    section("测试 4: chat() 同步调用")
    response = await provider.chat(
        messages=[ChatMessage(role="user", content="用 10 个字介绍 Raft 算法")],
        temperature=0.7,
        max_tokens=100,
    )
    print(f"  模型：{response.model}")
    print(f"  回复：{response.content}")
    print(f"  Token：input={response.input_tokens}, output={response.output_tokens}")
    print(f"  耗时：{response.latency_ms}ms")
    print(f"  费用：¥{response.cost_cny}")
    assert response.content, "回复不能为空"
    assert response.input_tokens > 0, "应该记录了 input tokens"
    print("  [PASS] chat() 正常返回")

    section("测试 5: stream_chat() 流式调用")
    print("  流式输出：", end="", flush=True)
    chunks = []
    async for chunk in provider.stream_chat(
        messages=[ChatMessage(role="user", content="说个'你好'")],
        temperature=0.7,
        max_tokens=50,
    ):
        chunks.append(chunk)
        print(chunk, end="", flush=True)
    print()
    full = "".join(chunks)
    assert full, "流式输出不能全空"
    print(f"  [PASS] 流式产出 {len(chunks)} 个 chunk，拼起来 = '{full}'")


async def main() -> int:
    # 加载 .env
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    if not os.getenv("ZHIPU_API_KEY") or os.getenv("ZHIPU_API_KEY") == "your_zhipu_api_key_here":
        print("[FAIL] ZHIPU_API_KEY 未设置，无法进行 API 测试")
        print(f"       请编辑 {env_path}")
        return 1

    try:
        test_config_loading()
    except Exception as e:
        print(f"  [FAIL] 配置加载失败：{e}")
        return 1

    # 实例化 provider（这一步本身验证了 base.py 的抽象类能被子类实例化）
    config = load_config()
    zhipu_cfg = next(p for p in config.providers if p.name == "zhipu")
    provider = ZhipuProvider(
        api_key=zhipu_cfg.config["api_key"],
        model=zhipu_cfg.config["model"],
    )
    # 验证 isinstance 关系：抽象类的意义就是类型契约
    assert isinstance(provider, LLMProvider), "ZhipuProvider 必须继承 LLMProvider"
    print(f"\n  [PASS] ZhipuProvider 正确实现 LLMProvider 抽象接口")

    try:
        await test_provider_basic(provider)
    except Exception as e:
        print(f"\n  [FAIL] Provider 测试失败：{e}")
        return 1

    section("全部测试通过！")
    print("  Phase 1 完成标志：")
    print("    [x] LLMProvider 抽象接口定义完成")
    print("    [x] ZhipuProvider 实现可实例化、可调用")
    print("    [x] 配置加载 + 环境变量替换工作正常")
    print("    [x] 同步 + 流式对话都跑通")
    print("    [x] check_health 和 estimate_cost 实现完整")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
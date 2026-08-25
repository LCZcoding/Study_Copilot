"""v0.5-1 验证脚本。

测试 QwenTurbo provider + Router：
1. QwenTurboProvider 单聊（需要 QWEN_API_KEY，否则 skip）
2. Router 默认走智谱（智谱可用时）
3. Router 失败切换（mock 智谱失败，验证用 qwen）
4. Router 全部失败抛 AllProvidersFailedError
5. estimate_cost 比较
6. Router check_health

运行：uv run python scripts/verify_v05_1.py
"""

import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import config  # noqa: F401  # 触发 .env 加载
from app.core.llm.base import ChatMessage
from app.core.llm.factory import create_router
from app.core.llm.qwen import QwenTurboProvider
from app.core.llm.router import AllProvidersFailedError, LLMRouter
from app.core.llm.zhipu import ZhipuProvider


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def assert_(condition: bool, msg: str) -> None:
    if condition:
        print(f"  [PASS] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        raise AssertionError(msg)


def has_qwen_key() -> bool:
    key = os.getenv("QWEN_API_KEY", "")
    return bool(key and key != "your_qwen_api_key_here")


def has_zhipu_key() -> bool:
    key = os.getenv("ZHIPU_API_KEY", "")
    return bool(key and key != "your_zhipu_api_key_here")


# ============ 测试 1：QwenTurboProvider 单聊 ============
async def test_qwen_direct() -> None:
    section("测试 1: QwenTurboProvider 直接调用（需要 QWEN_API_KEY）")
    if not has_qwen_key():
        print("  [SKIP] 未设置 QWEN_API_KEY")
        print("         注册地址：https://bailian.console.aliyun.com/")
        return

    provider = QwenTurboProvider(api_key=os.environ["QWEN_API_KEY"])
    response = await provider.chat(
        [ChatMessage(role="user", content="用一句话介绍 Raft 算法")],
        temperature=0.7,
        max_tokens=100,
    )
    print(f"  模型：{response.model}")
    print(f"  回复：{response.content[:80]}...")
    print(f"  Token：in={response.input_tokens}, out={response.output_tokens}")
    print(f"  费用：¥{response.cost_cny:.6f}")
    assert response.content
    assert response.input_tokens > 0
    assert response.cost_cny > 0, "qwen-turbo 应该收费"
    print("  [PASS] QwenTurbo 单聊成功")


# ============ 测试 2：Router 默认走智谱 ============
async def test_router_picks_zhipu() -> None:
    section("测试 2: Router 默认选智谱（智谱可用时）")
    if not has_zhipu_key():
        print("  [SKIP] 智谱 key 未设置")
        return

    zhipu = ZhipuProvider(api_key=os.environ["ZHIPU_API_KEY"])
    # Router 只有智谱也能工作
    router = LLMRouter([zhipu])
    print(f"  Provider 列表：{[(p.name, p.priority) for p in router.providers]}")

    response = await router.chat([ChatMessage(role="user", content="ping")])
    print(f"  实际用了：{response.model}")
    assert "glm" in response.model, "应该用智谱 GLM"
    print("  [PASS] Router 默认用智谱")


# ============ 测试 3：Router 失败切换 ============
async def test_router_fallback() -> None:
    section("测试 3: Router 失败 fallback（智谱坏掉 → 用 qwen）")
    if not has_qwen_key():
        print("  [SKIP] 需要 QWEN_API_KEY 才能验证 fallback")
        return

    # 构造一个"故意失败"的智谱（用错误的 API key）
    broken_zhipu = ZhipuProvider(api_key="INVALID_KEY_FORCE_FAILURE")
    qwen = QwenTurboProvider(api_key=os.environ["QWEN_API_KEY"])
    # priority: broken_zhipu=1 (先试)，qwen=2 (兜底)
    broken_zhipu.priority = 1
    qwen.priority = 2

    router = LLMRouter([broken_zhipu, qwen])
    print(f"  Provider 顺序：{[(p.name, p.priority) for p in router.providers]}")

    response = await router.chat([ChatMessage(role="user", content="ping")])
    print(f"  实际用了：{response.model}")
    assert "qwen" in response.model, "应该 fallback 到 qwen"
    print(f"  调用历史：{router.history}")
    assert any(h["result"] == "failure" for h in router.history), "应记录智谱失败"
    assert any(h["result"] == "success" for h in router.history), "应记录 qwen 成功"
    print("  [PASS] Fallback 正常切换")


# ============ 测试 4：Router 全部失败 ============
async def test_router_all_fail() -> None:
    section("测试 4: Router 全部失败抛 AllProvidersFailedError")
    broken_zhipu = ZhipuProvider(api_key="INVALID")
    broken_zhipu.priority = 1

    # 没有 qwen key → 跳过这个测试需要 qwen
    # 用两个都坏掉的 provider
    if has_qwen_key():
        broken_qwen = QwenTurboProvider(api_key="INVALID")
        broken_qwen.priority = 2
        router = LLMRouter([broken_zhipu, broken_qwen])
    else:
        router = LLMRouter([broken_zhipu])

    print(f"  Provider 顺序：{[(p.name, p.priority) for p in router.providers]}")
    try:
        await router.chat([ChatMessage(role="user", content="ping")])
        assert False, "应抛 AllProvidersFailedError"
    except AllProvidersFailedError as e:
        print(f"  捕获预期异常：{e}")
    print("  [PASS] 全部失败时正确抛错")


# ============ 测试 5：estimate_cost 比较 ============
def test_cost_estimation() -> None:
    section("测试 5: estimate_cost 比较")
    messages = [ChatMessage(role="user", content="这是一个测试问题" * 50)]

    zhipu = ZhipuProvider(api_key="dummy")
    print(f"  智谱预估成本：¥{zhipu.estimate_cost(messages)}")
    assert zhipu.estimate_cost(messages) == 0.0, "智谱免费"

    if has_qwen_key():
        qwen = QwenTurboProvider(api_key=os.environ["QWEN_API_KEY"])
        print(f"  Qwen 预估成本：¥{qwen.estimate_cost(messages)}")
        assert qwen.estimate_cost(messages) > 0, "Qwen 应收费"
    else:
        print("  [SKIP] qwen key 未设置，跳过 qwen 成本对比")
    print("  [PASS] 成本预估工作正常")


# ============ 测试 6：工厂从配置创建 Router ============
def test_factory_from_config() -> None:
    section("测试 6: 工厂函数从 YAML 配置创建 Router")
    # 模拟两个 enabled provider
    mock_configs = [
        # 这里直接构造 ProviderConfig 对象（绕过 yaml 加载）
        __import__("app.core.config", fromlist=["ProviderConfig"]).ProviderConfig(
            name="zhipu",
            type="chat",
            impl="ZhipuProvider",
            enabled=True,
            priority=1,
            config={"api_key": "DUMMY_ZHIPU_KEY"},
        ),
        __import__("app.core.config", fromlist=["ProviderConfig"]).ProviderConfig(
            name="qwen_turbo",
            type="chat",
            impl="QwenTurboProvider",
            enabled=True,
            priority=2,
            config={"api_key": "DUMMY_QWEN_KEY"},
        ),
    ]
    from app.core.llm.factory import create_router
    router = create_router(mock_configs)
    print(f"  Router providers：{[(p.name, p.priority) for p in router.providers]}")
    assert len(router.providers) == 2
    # 应该按 priority 排序：zhipu(1) 在前
    assert router.providers[0].name == "zhipu"
    assert router.providers[1].name == "qwen_turbo"
    print("  [PASS] 工厂正确创建 Router")


async def main() -> int:
    try:
        await test_qwen_direct()
        await test_router_picks_zhipu()
        await test_router_fallback()
        await test_router_all_fail()
        test_cost_estimation()
        test_factory_from_config()
    except AssertionError as e:
        print(f"\n  [OVERALL FAIL] {e}")
        return 1

    section("全部测试通过！")
    print("  v0.5-1 完成标志：")
    print("    [x] QwenTurboProvider 实现 LLMProvider 接口")
    print("    [x] LLMRouter 按 priority 调度")
    print("    [x] 失败 fallback：智谱坏掉 → qwen 接上")
    print("    [x] 全部失败 → AllProvidersFailedError")
    print("    [x] estimate_cost 区分免费 vs 付费")
    print("    [x] factory 从 YAML 配置创建 Router")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
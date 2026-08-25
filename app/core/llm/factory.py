"""Provider 工厂：从配置实例化 LLMProvider。

为什么需要工厂？配置里 `impl: "QwenTurboProvider"` 是字符串，
需要映射到真正的类。新增 provider 只需：
1. 写一个继承 LLMProvider 的类
2. 在 IMPL_MAP 里加一行
3. 在 config/llm.yaml 里加配置
"""

import inspect
from typing import Any, Dict, List

from .base import LLMProvider
from .qwen import QwenTurboProvider
from .zhipu import ZhipuProvider


# 字符串 → 类的映射。v0.5 启用：智谱 + qwen-turbo
IMPL_MAP = {
    "ZhipuProvider": ZhipuProvider,
    "QwenTurboProvider": QwenTurboProvider,
}

 
def _filter_config_for_constructor(cls: type, config: Dict[str, Any]) -> Dict[str, Any]:
    """过滤 config，只保留构造函数接受的参数。

    为什么需要：YAML 里有些字段（如 cost_per_1m_input）是"元数据"，
    用于调度决策但不应传给 provider 的 __init__。
    """

    # ========== inspect.signature() 详解 ==========
    #
    # Python 的 inspect 模块是"内省"工具，能在运行时检查代码结构。
    # signature(fn) 返回函数签名对象，可以拿到参数列表。
    #
    # 为什么需要这个：因为我们要"动态构造"对象，不能像写代码时那样
    # 知道每个类的 __init__ 签名。需要反射机制来"问"类它接受什么参数。
    #
    # 类比 Java 反射：
    # - Java: cls.getClass().getConstructors()[0].getParameters()
    # - Python: inspect.signature(cls.__init__).parameters
    #
    # 实际工作流程：
    # 1. inspect.signature(ZhipuProvider.__init__) 返回签名对象
    # 2. .parameters 是个有序字典，key 是参数名，value 是参数对象
    # 3. 我们只需要参数名（key），所以 .keys()
    sig = inspect.signature(cls.__init__)

    # ========== set() - {"self"} 详解 ==========
    #
    # 关键点：Python 调用类时（比如 `cls(api_key="...")`），
    # 我们只写 api_key、model 这些参数，看起来像是传 2 个参数。
    # 但 Python 内部会调用 __init__(self, api_key, model)——三个参数。
    # 那个 "self" 是 Python 自动加的（指向刚创建的空实例），不是我们传的。
    #
    # 所以 inspect.signature(cls.__init__).parameters 会返回 ["self", "api_key", "model"]
    # 我们要把 "self" 去掉，因为下面字典推导式只检查用户传的字段名。
    #
    # set() 是把列表/字典 key 转成集合：
    # - set(["self", "api_key", "model"]) - {"self"} = {"api_key", "model"}
    #
    # 为什么用 set 而不是 list：
    # - 后面用 `in` 检查，set 的 `in` 是 O(1)，list 是 O(n)
    # - 我们不会保留顺序（YAML 字段顺序不重要）
    accepted_params = set(sig.parameters.keys()) - {"self"}

    # ========== 字典推导式过滤详解 ==========
    #
    # `{k: v for k, v in config.items() if k in accepted_params}`
    # 等价于 Java:
    #   Map<String, Object> filtered = new HashMap<>();
    #   for (Map.Entry<String, Object> entry : config.entrySet()) {
    #       if (acceptedParams.contains(entry.getKey())) {
    #           filtered.put(entry.getKey(), entry.getValue());
    #       }
    #   }
    #
    # .items() 返回 (key, value) 元组列表，循环同时取 k 和 v
    # if k in accepted_params 过滤掉 __init__ 不接受的字段
    #
    # 实际场景示例：
    # 输入 config = {"api_key": "...", "model": "qwen-turbo",
    #               "cost_per_1m_input": 0.3, "cost_per_1m_output": 0.6}
    # QwenTurboProvider.__init__ 只接受 api_key 和 model
    # 输出 filtered = {"api_key": "...", "model": "qwen-turbo"}
    # cost_per_1m_input/output 被过滤掉，不会引发 TypeError
    return {k: v for k, v in config.items() if k in accepted_params}


def create_provider(name: str, impl: str, priority: int, config: dict) -> LLMProvider:
    """根据配置实例化单个 provider。

    Args:
        name: provider 标识名（如 "zhipu"）
        impl: 实现类名（如 "ZhipuProvider"）
        priority: 调度优先级（数字小=优先）
        config: provider 特有参数（api_key、model 等）
                  可包含额外的元数据字段（cost_* 等），会被自动过滤

    Raises:
        ValueError: 未注册的 impl 类名
    """
    cls = IMPL_MAP.get(impl)
    if cls is None:
        raise ValueError(
            f"未注册的 provider 实现：{impl}。"
            f"当前支持的：{list(IMPL_MAP.keys())}。"
            f"新增 provider 需在 IMPL_MAP 里注册。"
        )
    # 过滤掉 __init__ 不接受的字段（如 cost_per_1m_input 这种元数据）
    filtered_config = _filter_config_for_constructor(cls, config)
    provider = cls(**filtered_config)
    # 允许 provider 优先级被配置覆盖（默认从类属性读）
    provider.priority = priority
    return provider


def create_router(provider_configs: List) -> "LLMRouter":
    """从 ProviderConfig 列表创建 Router。

    Args:
        provider_configs: 来自 config/llm.yaml 的 ProviderConfig 列表（仅 enabled=True）

    Returns:
        LLMRouter 实例，按 priority 排序后的 provider 列表
    """
    from .router import LLMRouter

    enabled = [c for c in provider_configs if c.enabled]
    providers = [
        create_provider(
            name=c.name,
            impl=c.impl,
            priority=c.priority,
            config=c.config,
        )
        for c in enabled
    ]
    return LLMRouter(providers)
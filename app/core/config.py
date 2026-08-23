"""全局配置加载。

从 `config/llm.yaml` 读取 LLM provider 配置，支持 `${ENV_VAR}` 形式的占位符替换。
所有 provider 配置（启用、价格、模型名）都从这里统一管理，业务代码不直接读 yaml。
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """单个 LLM Provider 的配置。

    Java 类比：类似一个配置类，字段名跟 YAML 对应。
    Pydantic 自动做类型转换和校验，写错字段名（比如写成 `porvider`）会立即报错。
    """

    name: str
    type: str  # "chat" 表示对话模型，"embedding" 表示向量模型（v0.1 暂未启用 embedding provider 配置）
    impl: str  # 实现类名，如 "ZhipuProvider"
    enabled: bool
    priority: int = 1  # 数字越小优先级越高
    config: Dict[str, Any]  # 实现类特有的参数（api_key、model 等）


class LLMConfig(BaseModel):
    """完整的 LLM 配置。

    providers: 对话模型列表
    embeddings: 向量模型列表（v0.1 暂未启用，预留）
    """

    providers: List[ProviderConfig]
    embeddings: List[ProviderConfig] = []  # 默认为空列表


def _substitute_env(obj: Any) -> Any:
    """递归替换 YAML 字符串中的 ${VAR_NAME} 占位符为环境变量值。

    这是 Spring Boot `${ENV_VAR:default}` 的 Python 版实现。

    Java 类比：跟 Spring 的 PropertyPlaceholderConfigurer 类似，但更轻量（不用引 Spring）。
    """
    # 正则匹配 ${SOME_VAR} 形式（变量名必须是大写字母+下划线+数字，标准约定）。
    pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        # 如果环境变量未设置，保留原始占位符（方便排查"哪个变量忘了"）。
        # 而不是静默替换为空字符串——那会让 API 调用报 401 而找不到原因。
        return os.environ.get(var_name, match.group(0))

    if isinstance(obj, str):
        return pattern.sub(replace, obj)
    elif isinstance(obj, dict):
        return {k: _substitute_env(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_env(v) for v in obj]
    return obj


def load_config(config_path: Path | None = None) -> LLMConfig:
    """从 YAML 文件加载配置。

    Args:
        config_path: 配置文件路径，默认是 `config/llm.yaml`。
                      允许测试时传临时文件（方便 mock）。

    Java 类比：跟 Spring 的 @ConfigurationProperties 注入类似。
    """
    if config_path is None:
        # Path(__file__).parent.parent.parent = app/core/config.py -> app/core -> app -> 项目根
        config_path = Path(__file__).parent.parent.parent / "config" / "llm.yaml"

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 替换 ${VAR} 占位符
    raw = _substitute_env(raw)

    return LLMConfig(**raw)


# 模块级单例：进程启动时加载一次，全局复用。
# Python 没有 Spring 的 @Autowired，但模块级变量就是天然的全局单例。
# 注意：这意味着 .env 必须在 import 这个模块前加载（dotenv.load_dotenv()）。
config = load_config()
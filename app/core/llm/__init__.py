"""LLM Provider 抽象层。

设计原则：
- 所有 LLM 供应商统一实现 LLMProvider 接口
- 配置驱动启用/禁用，新增 Provider 只需加配置 + 实现类
- v0.1 只接入智谱一家，配置文件中预留 qwen-turbo / qwen-long 扩展位
"""
"""智谱 GLM-4-Flash API 冒烟测试。
解释冒烟测试：
主要用于确保软件系统的基本功能是正常的，以便其他更详细的测试可以进行。
类似电子产品通电，看冒不冒烟

目的：验证 API key 有效 + 理解基本调用方式。
用法：
    1. 把 .env.example 复制为 .env，填入 ZHIPU_API_KEY
    2. 运行：uv run python scripts/test_zhipu.py
"""

import os
import sys
from pathlib import Path 

# Windows 默认 GBK 编码，强制 stdout 用 UTF-8 才能正常打印 emoji
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 让脚本能找到 app 包（即使从项目根目录运行也能用）
# 第一个parent表示当前文件的文件夹scripts，第二个表示再上一级的根目录，0表示放入索引首部
sys.path.insert(0, str(Path(__file__).parent.parent)) 

from dotenv import load_dotenv
from zhipuai import ZhipuAI


def main() -> int:
    # 加载 .env
    # 重载了运算符‘/’优雅拼接路径
    env_path = Path(__file__).parent.parent / ".env" 
    load_dotenv(env_path) # 加载env到环境变量

    api_key = os.getenv("ZHIPU_API_KEY") # 使用环境变量通过key得到value
    if not api_key or api_key == "your_zhipu_api_key_here":
        print("❌ 错误：未设置 ZHIPU_API_KEY")
        print(f"   请编辑 {env_path} 填入你的 API key")
        print("   注册地址：https://bigmodel.cn/")
        return 1

    print("🚀 正在调用智谱 GLM-4-Flash...")
    client = ZhipuAI(api_key=api_key) # 形参=实参 传递参数

    try:
        response = client.chat.completions.create(
            model="glm-4-flash",
            messages=[
                {"role": "user", "content": "用一句话介绍 Raft 共识算法。"},
            ],
            temperature=0.7,
            max_tokens=500,
        )
    except Exception as e:
        print(f"❌ API 调用失败：{e}")
        print("   可能原因：API key 错误 / 网络问题 / 账户余额不足")
        return 1

    # 解析响应
    content = response.choices[0].message.content
    usage = response.usage

    print("\n✅ 调用成功！\n")
    print(f"📝 模型回复：\n{content}\n")
    if usage:
        print(f"📊 Token 用量：")
        print(f"   - 输入 tokens：{usage.prompt_tokens}")
        print(f"   - 输出 tokens：{usage.completion_tokens}")
        print(f"   - 总计：{usage.total_tokens}")
        print(f"💰 费用：¥0.00（GLM-4-Flash 完全免费）")

    return 0

# __name__变量自动生成，当该脚本被执行时，值自动变为__main__
if __name__ == "__main__":
    sys.exit(main()) # 先执行main（）函数，再退出，输出返回值
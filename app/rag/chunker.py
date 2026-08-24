"""文档切片器。

把一长段文本切成多个小段，方便后续 Embedding 和检索。
v0.1 用最简单的固定字符数切片 + overlap，不做智能边界检测。
overlap：两个片段重叠一部分防止，切分导致语义断裂

为什么需要切？
- Embedding 模型有最大长度限制（bge-m3 是 8192 tokens，约 3-4 万字）
- 检索粒度更细：500 字的片段比整篇文档更容易精准匹配问题
- LLM 上下文窗口有限：检索出来的 top-k 片段必须能塞进 prompt

为什么 overlap？
- 防止关键信息正好被切在边界（比如"领导者选举"被切成"领导者"+"选举"两段）
- 50 字 overlap 让相邻片段共享 50 字上下文
"""

from typing import Dict, List


def chunk_document(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> List[Dict]:
    """把长文本切成多个小段。

    Args:
        text: 完整文本内容
        chunk_size: 每段字符数（默认 500）
        overlap: 相邻片段重叠字符数（默认 50，必须 < chunk_size）

    Returns:
        列表，每项是 {"id": int, "text": str}。
        id 从 0 开始递增，便于后续定位和溯源。

    Raises:
        ValueError: 参数非法（overlap >= chunk_size）

    Java 类比：类似 Guava 的 Strings.substring 滑动窗口，但带 overlap。
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须 > 0，实际 {chunk_size}")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError(
            f"overlap 必须在 [0, chunk_size) 区间，实际 overlap={overlap}, chunk_size={chunk_size}"
        )

    text = text.strip()
    if not text:
        return []  # 空文档直接返回空列表，不抛错（调用方决定是否报错）

    chunks: List[Dict] = []
    # step 是每次滑动的距离。chunk_size=500, overlap=50 → step=450，
    # 即每段起点比上一段前进 450 字，相邻共享最后 50 字。
    step = chunk_size - overlap
    n_chars = len(text)

    chunk_id = 0
    start = 0

    while start < n_chars:
        end = min(start + chunk_size, n_chars)
        chunk_text = text[start:end]

        # 跳过完全空白的片段（可能出现在 overlap 边界上）
        if chunk_text.strip():
            chunks.append({"id": chunk_id, "text": chunk_text})
            chunk_id += 1

        # 如果已经到末尾，退出；否则前进 step
        if end >= n_chars:
            break
        start += step

    return chunks
"""文档加载器。

负责把各种格式的文件（PDF、Markdown、TXT）转成统一的纯文本字符串。
上层（RAG 链路）只关心"一整段文字"，不关心格式。

v0.1 支持：
- PDF：pypdf 抽取每页文字
- MD / TXT：chardet 自动检测编码后 decode

v0.1 不做（防 scope creep）：
- 扫描版 PDF（需要 OCR，方案留给 v0.5+）
- DOCX / HTML / EPUB（v0.5+ 按需加）
- 图片 / 表格抽取
"""

import io
from pathlib import Path

# pypdf 是纯 Python 的 PDF 文本提取库。Java 类比：类似 Apache PDFBox，但纯 Python 不需要 JVM。
from pypdf import PdfReader

# chardet 自动检测文件编码（GBK / UTF-8 / Latin-1 等），避免打开 TXT 时乱码。
# 类似 Java 的 juniversalchardet。
import chardet


SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}


class DocumentLoadError(Exception):
    """文档加载失败。包装底层异常，方便上层捕获后给用户友好提示。"""


def load_document(content: bytes, filename: str) -> str:
    """根据文件名后缀分发到不同的加载器，返回纯文本内容。

    Args:
        content: 文件二进制内容（用 UploadFile.read() 拿到）
        filename: 原始文件名（含扩展名），用于判断格式

    Returns:
        提取出的纯文本字符串

    Raises:
        DocumentLoadError: 格式不支持 / 文件损坏 / 解码失败

    Java 类比：类似策略模式 + 工厂方法，根据文件类型选择不同的 Parser。
    """
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        return _load_pdf(content)
    elif suffix in {".md", ".markdown", ".txt"}:
        return _load_text(content, suffix)
    else:
        raise DocumentLoadError(
            f"不支持的文件格式：{suffix}。"
            f"v0.1 仅支持 {sorted(SUPPORTED_EXTENSIONS)}"
        )


def _load_pdf(content: bytes) -> str:
    """从 PDF 二进制提取文字。

    实现说明：用 io.BytesIO 把 bytes 包成文件对象给 pypdf，
    pypdf 内部逐页解析，每页调 .extract_text()。
    """
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as e:
        raise DocumentLoadError(f"PDF 解析失败（文件可能损坏）：{e}") from e

    # 逐页抽取，用换行符连接。Java 类比：类似拼接 PDFTextStripper 的输出。
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(page_text)
        # 空页（纯图片）静默跳过，不报错——v0.1 不支持 OCR，扫描版 PDF 会得到空文本

    if not pages:
        raise DocumentLoadError(
            "PDF 中未提取到任何文字。如果是扫描版 PDF（图片），"
            "v0.1 不支持 OCR，请用其他文档测试。"
        )

    return "\n\n".join(pages)


def _load_text(content: bytes, suffix: str) -> str:
    """从 MD / TXT 文本文件提取内容。

    关键点：编码检测。Windows 上保存的 TXT 经常是 GBK，Linux/Mac 多是 UTF-8，
    不检测直接 decode 会乱码。
    """
    # chardet.detect 返回 {"encoding": "utf-8", "confidence": 0.99, ...}
    detection = chardet.detect(content)
    encoding = detection.get("encoding")

    if not encoding:
        raise DocumentLoadError(
            f"无法检测文件编码（chardet 置信度太低）：{detection}"
        )

    try:
        text = content.decode(encoding)
    except (UnicodeDecodeError, LookupError) as e:
        # LookupError：encoding 是 chardet 瞎猜的非法名字（如 'ascii' 实际不存在）
        raise DocumentLoadError(
            f"用 {encoding} 解码失败：{e}。可能文件编码特殊。"
        ) from e

    return text


def detect_format(filename: str) -> str:
    """辅助函数：根据文件名推断文档类型，用于日志和用户提示。

    Returns:
        "pdf" | "markdown" | "text" | "unknown"
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    elif suffix in {".md", ".markdown"}:
        return "markdown"
    elif suffix == ".txt":
        return "text"
    return "unknown"
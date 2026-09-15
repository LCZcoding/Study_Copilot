"""落盘文件名工具（纯函数，无外部依赖）。

飞书 space 名 / 文档标题里可能有任意字符（斜杠、全角符号、emoji…），
直接拿来当文件名会在 Windows 上炸，这里统一清洗并加上防重名的 token 前缀。
"""
import re
import unicodedata

# Windows / Linux 都禁用的文件名字符。Windows 还禁 < > : " | ? *，加 NUL。
# 参考：https://learn.microsoft.com/windows/win32/fileio/naming-a-file
_INVALID_FNAME_CHARS = re.compile(r'[\\/:*?"<>|\x00]')
_MULTI_UNDERSCORE = re.compile(r"_+")
_MAX_TITLE_LEN = 80


def sanitize_filename(name: str) -> str:
    """把任意字符串清理成合法文件名片段。

    规则：
    - NFKC 归一化（处理全角字符，比如「／」→「/」→ '_'）
    - 去掉 / 替换非法字符 \\ / : * ? " < > | \\0 为 _
    - 合并连续下划线，去首尾下划线/空格/点
    - 截断到 80 字符
    - 全空返 'untitled'
    """
    if not name:
        return "untitled"
    name = unicodedata.normalize("NFKC", name).strip()
    name = _INVALID_FNAME_CHARS.sub("_", name)
    name = _MULTI_UNDERSCORE.sub("_", name).strip("_. ")
    if len(name) > _MAX_TITLE_LEN:
        name = name[:_MAX_TITLE_LEN].rstrip("_. ")
    return name or "untitled"


def build_export_filename(space_name: str, title: str, node_token: str) -> str:
    """导出文件的最终名：<space>__<title>__<token8>.md。

    末尾加 node_token 前 8 位，防止不同 space 出现同名文档时覆盖。
    """
    s = sanitize_filename(space_name)
    t = sanitize_filename(title)
    prefix = (node_token or "no000000")[:8]
    return f"{s}__{t}__{prefix}.md"


def build_export_dirname(space_name: str, space_id: str) -> str:
    """导出目录名：<sanitized_space_name>_<space_id前8位>。

    加 space_id 前缀防：
    - 不同 space 重名（虽然概率低）
    - Windows 上中文目录 + Git Bash 显示差异
    - 用户改 space_name 后落盘目录还能识别
    """
    s = sanitize_filename(space_name)
    prefix = (space_id or "no000000")[:8]
    # 如果 sanitize 后变 'untitled'，至少还有 id 前缀可识别
    return f"{s}_{prefix}"

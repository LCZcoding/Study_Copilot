"""RAG 服务的 HTTP 适配器。

同步脚本只通过这一层跟本机 RAG 服务（app/api）打交道：
- check_rag_service：同步前看库现状（健康检查 + chunks 数）
- upload_to_rag：POST /api/upload 入库（服务端解析 + embed）
- delete_document_from_rag：DELETE /api/documents/... 删除传播
"""
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

import httpx

# 跟 app/data/loader.py 的 SUPPORTED_EXTENSIONS 对齐。
# 脚本只产 .md（飞书 markdown），但留 .markdown/.txt 兜底。
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
_MIME_MAP = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


def upload_to_rag(
    service_url: str,
    files: List[Path],
    timeout: int = 120,
    show_progress: bool = True,
) -> dict:
    """串行上传 md/txt 文件到 RAG /upload。

    返回：{"uploaded": int, "failed": [...], "skipped": [...], "total_chunks": int}
    """
    result = {"uploaded": 0, "failed": [], "skipped": [], "total_chunks": 0}
    total = len(files)

    for idx, file_path in enumerate(files, start=1):
        suffix = file_path.suffix.lower()

        if suffix not in SUPPORTED_SUFFIXES:
            reason = f"后缀 {suffix} 不在 loader 白名单"
            result["skipped"].append({"file": file_path.name, "reason": reason})
            if show_progress:
                print(f"  ⊘ [{idx}/{total}] {file_path.name}（{reason}）")
            continue

        mime = _MIME_MAP[suffix]

        try:
            with httpx.Client(timeout=timeout) as client:
                with open(file_path, "rb") as fp:
                    resp = client.post(
                        f"{service_url}/api/upload",
                        files={"file": (file_path.name, fp, mime)},
                    )
        except httpx.TimeoutException:
            err = f"timeout {timeout}s"
            result["failed"].append({"file": file_path.name, "error": err})
            if show_progress:
                print(f"  ✗ [{idx}/{total}] {file_path.name}：{err}")
            continue
        except httpx.HTTPError as e:
            result["failed"].append({"file": file_path.name, "error": str(e)})
            if show_progress:
                print(f"  ✗ [{idx}/{total}] {file_path.name}：{e}")
            continue

        if resp.status_code == 200:
            body = resp.json()
            chunks = body.get("chunks", 0)
            result["uploaded"] += 1
            result["total_chunks"] += chunks
            if show_progress:
                print(f"  ✓ [{idx}/{total}] {file_path.name}：{chunks} chunks")
        else:
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            result["failed"].append({"file": file_path.name, "error": err})
            if show_progress:
                print(f"  ✗ [{idx}/{total}] {file_path.name}：{err[:100]}")

    return result


def check_rag_service(service_url: str, timeout: int = 5) -> Optional[dict]:
    """调 GET /api/documents 看 RAG 服务现状。失败返 None（不打 error，让上层决定）。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{service_url}/api/documents")
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        return None
    return None


def delete_document_from_rag(
    service_url: str,
    source_name: str,
    source_type: str = "file",
    timeout: int = 30,
) -> dict:
    """调 DELETE /api/documents/{type}/{name}，删一个文档的全部 chunks。

    返回：{"deleted": bool, "already_gone": bool, "error": Optional[str]}

    三种结果的处理（增量同步的容错原则：远端失败绝不销账）：
    - 200 → deleted=True，真删了，可以销 manifest 的账
    - 404 → already_gone=True，库里本来就没这条（手动清过/上次删了没记账），
            同样视为"删除完成"，销账让两边对齐
    - 其他状态码 / 网络异常 → error 非空，调用方必须保留 manifest 记录，
            下次 sync 重试。绝不能在没删成功时销账，否则旧 chunks 永久残留
    """
    # source_name 是导出文件名（含中文/括号/逗号），路径参数必须 percent-encode，
    # safe='' 让 '/' 等也被编码——文件名里不该出现路径分隔符
    url = (
        f"{service_url}/api/documents/"
        f"{quote(source_type)}/{quote(source_name, safe='')}"
    )
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.delete(url)
    except httpx.HTTPError as e:
        return {"deleted": False, "already_gone": False, "error": str(e)}

    if resp.status_code == 200:
        return {"deleted": True, "already_gone": False, "error": None}
    if resp.status_code == 404:
        return {"deleted": False, "already_gone": True, "error": None}
    return {
        "deleted": False,
        "already_gone": False,
        "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
    }

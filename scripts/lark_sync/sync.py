"""同步编排层：把"飞书 wiki → 落盘 → RAG 库"的完整用例串起来。

流程：
    0. 同步前检查 RAG 服务现状（非 dry-run）
    1. 认证检查（失败只 warn，不强退）
    2. 确定要同步的 space 列表（--space 指定 / 默认只取企业公开）
    3. 逐 space：列 nodes → fetch markdown → hash 比对 → 落盘
    4. 上传新/变更文件（失败回滚 manifest）
    5. 删除传播（飞书已删/改名 → DELETE RAG → 销账）
    6. manifest 落盘 + 汇总

本层只做"决策与编排"，所有外部交互都在四个叶子模块里：
    lark_cli / rag_client / manifest / filenames
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .filenames import build_export_dirname, build_export_filename
from .lark_cli import LarkCLI, LarkCLIError
from .manifest import (
    DEFAULT_MANIFEST_PATH,
    content_hash,
    load_manifest,
    save_manifest,
)
from .rag_client import (
    check_rag_service,
    delete_document_from_rag,
    upload_to_rag,
)


@dataclass
class SyncConfig:
    """一次同步运行的全部配置（由入口的 argparse 构造，方便单测直接 new）。"""

    service_url: str = "http://localhost:8000"
    output_dir: Path = Path("./lark-exports")
    manifest_path: Path = DEFAULT_MANIFEST_PATH
    lark_bin: Optional[str] = None
    # 指定只同步这些 space_id；None 表示列出全部后按可见性过滤
    spaces: Optional[List[str]] = None
    dry_run: bool = False
    doc_format: str = "markdown"       # xml / markdown / im-markdown
    scope: str = "full"               # full / outline / range / keyword / section
    assume_yes: bool = False          # 跳过"库已有 chunks"确认
    include_private: bool = False     # 默认只同步企业公开
    delete_propagation: bool = True   # 飞书删除是否传播到 RAG
    # upload 单文件超时秒数（文档大时可调，暂未暴露 CLI）
    upload_timeout: int = 120


def run_sync(cfg: SyncConfig) -> int:
    """执行一次完整同步。返回进程退出码：0 成功，1 有失败。"""

    # 0. dry-run 跳过 / 否则查 RAG 现状
    if not cfg.dry_run:
        existing = check_rag_service(cfg.service_url)
        if existing is None:
            print(f"⚠️  无法连接 RAG 服务 {cfg.service_url}（服务没启动？）")
            print(f"   上传阶段会失败，但会先把 .md 落盘到 {cfg.output_dir}/")
        else:
            total_chunks = existing.get("total_chunks", 0)
            doc_count = len(existing.get("documents", []))
            if total_chunks > 0:
                print(f"⚠️  RAG 库里已有 {total_chunks} 个 chunks（{doc_count} 个文档）")
                print("   每次 POST /upload 会触发 fetch_documents() 全量 re-parse + re-embed。")
                print("   重复上传会让 chunks 翻倍（服务端没去重逻辑）。")
                if not cfg.assume_yes:
                    print()
                    try:
                        input("按 Enter 继续，Ctrl+C 退出... ")
                    except EOFError:
                        print("⚠️  非交互环境，跳过确认（用 --yes 显式跳过这个 prompt）")

    # 1. 实例化 LarkCLI
    try:
        cli = LarkCLI(lark_bin=cfg.lark_bin)
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1
    print("✓ Lark CLI 已检测到")

    # 2. 认证检查（不强失败，warn 后继续）
    try:
        auth = cli.auth_status()
        identity = auth.get("identity", "unknown")
        print(f"✓ 已登录 Lark CLI（identity={identity}）")
    except (RuntimeError, LarkCLIError) as e:
        print(f"⚠️  查 auth 失败：{e}")
        print("   继续尝试列 wiki（可能 list 时也会失败）")

    # 3. 确定要同步的 space 列表
    if cfg.spaces:
        spaces = [{"space_id": sid, "name": f"space_{sid[:8]}"} for sid in cfg.spaces]
        print(f"✓ 用 --space 指定 {len(spaces)} 个 space")
    else:
        try:
            all_spaces = cli.list_wiki_spaces()
        except LarkCLIError as e:
            print(f"❌ 列 wiki 失败（业务错）：{e}")
            print("   可能 scope 不够 → 重新跑 lark-cli auth login --recommend")
            return 1
        except RuntimeError as e:
            print(f"❌ 列 wiki 失败（命令错）：{e}")
            return 1

        if cfg.include_private:
            spaces = all_spaces
            print(f"✓ 找到 {len(spaces)} 个 wiki space（--include-private：包含私有）")
        else:
            # 默认只同步企业公开：user 身份能列出所有有权限的 space（含个人私有），
            # 不过滤会把个人文档也灌进知识库。bot 身份时代"只见公开"是身份
            # 造成的假象，现在必须显式过滤。
            spaces = [s for s in all_spaces if s.get("visibility") == "public"]
            skipped = len(all_spaces) - len(spaces)
            print(f"✓ 共找到 {len(all_spaces)} 个 space，默认只同步企业公开："
                  f"保留 {len(spaces)} 个，跳过 {skipped} 个私有"
                  f"（想包含私有加 --include-private）")

    if not spaces:
        print("ℹ️  没有 wiki 可同步。可能原因：")
        print("   1. space 都是私有的（默认只同步企业公开；想包含私有加 --include-private）")
        print("   2. 没给 Lark CLI 正确 scope（重新跑 lark-cli auth login --recommend）")
        return 0

    # 4. 逐 space 列 nodes + fetch + 落盘
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    written_files: List[Path] = []
    by_type: dict = {}            # {obj_type: count}
    skipped_by_type: dict = {}    # {obj_type: [title, ...]}

    # 读同步状态
    # docs_meta 结构：{文件名: {"content_hash", "space_id", "obj_token", "title", "synced_at"}}
    # 它是"上次同步后，每篇文档在向量库里的真实状态"
    manifest = load_manifest(cfg.manifest_path)
    docs_meta = manifest["documents"]
    unchanged_count = 0  # 内容没变、跳过上传的文档数

    # 删除传播用的两份"本次同步现场"记录：
    # seen_fnames：本次在飞书侧实际见到的 docx 文档（含 fetch 失败的——
    #   拉取失败 ≠ 文档被删，不能因为网络抖动误删库里的文档）
    # active_space_ids：node-list 成功的 space。只有这些 space 里"没见到"
    #   才算真的被删；列节点失败 / 被 --space 或私有过滤排除的 space 整组保护
    seen_fnames: set = set()
    active_space_ids: set = set()
    space_name_by_id: dict = {}  # sid -> sname，删本地落盘文件时拼目录名

    for space in spaces:
        sid = space.get("space_id") or space.get("id") or ""
        if not sid:
            print(f"  ⚠️  space 缺 space_id，跳过：{space}")
            continue
        sname = space.get("name") or sid[:8]

        print(f"\n--- Space: {sname} ({sid[:12]}...) ---")

        try:
            nodes = cli.list_wiki_nodes(sid)
        except LarkCLIError as e:
            print(f"  ✗ 列节点失败：{e}")
            continue
        except RuntimeError as e:
            print(f"  ✗ 列节点命令失败：{e}")
            continue

        print(f"  找到 {len(nodes)} 个节点")
        # node-list 成功 → 这个 space 的节点列表是"权威全集"，
        # 里面缺了的文档才是真被删了（删除传播的资格标记）
        active_space_ids.add(sid)
        space_name_by_id[sid] = sname

        for node in nodes:
            obj_type = node.get("obj_type", "unknown")
            obj_token = node.get("obj_token") or ""
            title = node.get("title") or "(无标题)"
            node_token = node.get("node_token") or ""

            by_type[obj_type] = by_type.get(obj_type, 0) + 1

            # 本期只支持 docx
            if obj_type != "docx":
                skipped_by_type.setdefault(obj_type, []).append(title)
                print(f"    ⊘ {title}（obj_type={obj_type}，v0.5-2 只支持 docx）")
                continue

            if not obj_token:
                print(f"    ⚠️  {title} 缺少 obj_token，跳过")
                continue

            # fname 只依赖 sname/title/node_token，fetch 之前就能算。
            # 先标记 seen：只要飞书节点列表里还有这篇文档，它就不是"被删除"，
            # 哪怕下面 fetch 失败也不能进删除传播名单（拉取失败 ≠ 文档删除）
            fname = build_export_filename(sname, title, node_token)
            seen_fnames.add(fname)

            try:
                md = cli.fetch_document_markdown(
                    obj_token,
                    doc_format=cfg.doc_format,
                    scope=cfg.scope,
                )
            except LarkCLIError as e:
                print(f"    ✗ {title} fetch 失败：{e}")
                continue
            except RuntimeError as e:
                print(f"    ✗ {title} fetch 异常：{e}")
                continue

            dname = build_export_dirname(sname, sid)
            out_path = cfg.output_dir / dname / fname
            # 先比对后落盘——内容没变则完全零操作（不落盘、不上传、不记账）
            # 跳过落盘是安全的：内容没变 = 本地文件上次写的就是这个内容，重写是纯重复
            # （唯一边界：本地文件被手动删了但内容没变 → 不会重新生成。
            #   lark-exports 只是 debug 备份，可接受）
            new_hash = content_hash(md)
            old = docs_meta.get(fname)
            if old and old.get("content_hash") == new_hash:
                unchanged_count += 1
                print(f"    ⊘ {title}（内容没变，跳过落盘和 upload）")
                continue

            # 内容变了（或新文档）：落盘 + 记账 + 进上传列表
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md, encoding="utf-8")
            except OSError as e:
                print(f"    ✗ {title} 写盘失败：{e}")
                continue

            # 先记新 hash 进 manifest 候选
            # 注意是"先记账"——上传失败后要回滚（见上传后处理），不能漏。
            # 原则：manifest 只能记"已成功入库"的状态，这里只是候选
            docs_meta[fname] = {
                "content_hash": new_hash,
                "space_id": sid,
                "obj_token": obj_token,
                "title": title,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
            written_files.append(out_path)
            print(f"    ✓ {title} → {out_path.relative_to(cfg.output_dir)} ({len(md)} chars)")

    # 5. 汇总
    print(f"\n=== 落盘汇总 ===")
    print(f"  写入文件：{len(written_files)}")
    for t, c in sorted(by_type.items()):
        if t in skipped_by_type:
            print(f"  - obj_type={t}: {c}（跳过 {len(skipped_by_type[t])}）")
        else:
            print(f"  - obj_type={t}: {c}")

    # 删除传播：找出"manifest 账上有、飞书已没有"的文档。
    # 两个条件同时满足才判删（缺一不可，防误删）：
    #   1) 所属 space 本次 node-list 成功（active）——列出失败 / 被 --space、
    #      私有过滤排除的 space 整组保护，缺了文档也可能只是"没看到"
    #   2) 本次节点列表里没见到这个 fname
    # 改名文档会自然落进名单：新标题=新 fname（走新增上传），旧 fname 消失（走删除）
    stale_entries = [
        (stale_name, meta)
        for stale_name, meta in docs_meta.items()
        if stale_name not in seen_fnames
        and meta.get("space_id") in active_space_ids
    ]
    # 账上有、但所属 space 本次没成功遍历的文档数（仅用于汇总提示）
    protected_count = sum(
        1 for meta in docs_meta.values()
        if meta.get("space_id") not in active_space_ids
    )

    if cfg.dry_run:
        # dry-run 不上传 / 不删除 / 不更新 manifest——没真正做的事都不能记账
        print("  (dry-run 模式，跳过上传和删除，manifest 不更新)")
        for stale_name, _ in stale_entries:
            print(f"    [would-delete] {stale_name}")
        if stale_entries:
            print(f"  正式运行时以上 {len(stale_entries)} 个文档会从 RAG 删除")
        return 0

    # 6. 上传（没有变更文件就跳过；注意不能直接 return——
    #    即使没有新增，删除传播仍需照常执行）
    if written_files:
        print(f"\n--- 上传 {len(written_files)} 个文件到 {cfg.service_url} ---")
        result = upload_to_rag(
            cfg.service_url, written_files, timeout=cfg.upload_timeout
        )

        # 上传失败的文档回滚 manifest 记录
        # 为什么必须回滚：fetch 时已把新 hash 记进 docs_meta，上传失败不撤销的话，
        # 下次 sync 一比 hash——"没变"→ 跳过 → 该文档永远不同步。
        # 这是增量同步最经典的坑：manifest 是"入库成功"的账本，失败的账要销
        if result["failed"]:
            failed_names = {f["file"] for f in result["failed"]}
            for failed_name in failed_names:
                if docs_meta.pop(failed_name, None) is not None:
                    print(f"  ⚠️  {failed_name} 上传失败，manifest 已回滚（下次 sync 重试）")
    else:
        print("\n  没有需要上传的新/变更文件")
        result = {"uploaded": 0, "failed": [], "skipped": [], "total_chunks": 0}

    # 7. 删除传播：把飞书侧已移除的文档从 RAG 库和 manifest 中摘掉
    deleted_count = 0
    already_gone_count = 0
    delete_failed: List[dict] = []

    if stale_entries and not cfg.delete_propagation:
        print(f"\n⊘ 删除传播已关闭：{len(stale_entries)} 个文档在飞书已不存在，"
              f"保留在 RAG 库（--no-delete-propagation）")
    elif stale_entries:
        print(f"\n--- 删除传播：{len(stale_entries)} 个文档在飞书已不存在 ---")
        for stale_name, meta in stale_entries:
            r = delete_document_from_rag(cfg.service_url, stale_name)
            if r["error"]:
                # 远端失败绝不销账：保留 manifest 记录，下次 sync 重试。
                # 一旦销账，旧 chunks 就再也没人知道、永久残留
                delete_failed.append({"file": stale_name, "error": r["error"]})
                print(f"  ✗ {stale_name} 删除失败，保留 manifest 下次重试："
                      f"{r['error'][:100]}")
                continue

            # 200（真删了）或 404（库里本就没有）都算删除完成 → 销账
            docs_meta.pop(stale_name, None)

            # 本地落盘文件 best-effort 清理（debug 备份，删不掉不影响主流程）
            stale_sid = meta.get("space_id") or ""
            stale_sname = space_name_by_id.get(stale_sid)
            if stale_sname:
                local_path = (
                    cfg.output_dir
                    / build_export_dirname(stale_sname, stale_sid)
                    / stale_name
                )
                try:
                    local_path.unlink(missing_ok=True)
                except OSError:
                    pass

            if r["already_gone"]:
                already_gone_count += 1
                print(f"  ⊘ {stale_name}（RAG 库中已不存在，仅清理 manifest 账本）")
            else:
                deleted_count += 1
                print(f"  ✓ {stale_name}（已从 RAG 删除）")

    # 8. 账本落盘：上传成功/失败回滚 + 删除销账都改在内存 manifest 上，统一持久化
    save_manifest(manifest, cfg.manifest_path)

    print(f"\n=== 完成 ===")
    print(f"  上传成功：{result['uploaded']}")
    print(f"  上传失败：{len(result['failed'])}")
    print(f"  未变更（跳过 upload）：{unchanged_count}")
    if result["skipped"]:
        print(f"  跳过：{len(result['skipped'])}")
    print(f"  总 chunks：{result['total_chunks']}")
    print(f"  删除传播：已删 {deleted_count}，库中本不存在 {already_gone_count}，"
          f"删除失败 {len(delete_failed)}")
    if protected_count:
        print(f"  受保护未判删：{protected_count}"
              f"（所属 space 本次被过滤或列出失败，属正常保护）")
    if result["failed"]:
        print(f"\n  上传失败的文件：")
        for f in result["failed"]:
            print(f"    - {f['file']}：{f['error'][:100]}")
    if delete_failed:
        print(f"\n  删除失败的文件（下次 sync 自动重试）：")
        for f in delete_failed:
            print(f"    - {f['file']}：{f['error'][:100]}")

    return 0 if not result["failed"] and not delete_failed else 1

# 飞书内容同步到 RAG

v0.5-2 D ext：用飞书官方 [Lark CLI](https://github.com/larksuite/cli) 把飞书 wiki 导出到本地，再通过 `/upload` 入库。

## 为什么不用 OAuth

飞书自建应用读个人 wiki 受 scope 限制（之前测过 JWT 只有 `auth:user.id:read`）。

Lark CLI 是飞书官方工具，OAuth 在 CLI 端解决（用户浏览器登录），我们只消费导出文件。

## `visibility: public` 的真实副作用（必读）

Lark CLI / 飞书 API 能读 wiki 的前置条件是 wiki 的 **"知识库访问级别"** 设为 `public`（企业公开）。
但 **`public` 在飞书的产品实现里 = 两件事同时发生**：

| 后果 | 风险 |
|---|---|
| ✅ Lark CLI / API 用对应 scope 能读（这是 sync 脚本要的效果） | 正面 |
| ⚠️ **你们企业外的飞书用户**通过飞书搜索也能搜到 + 查看内容 | **要意识到** |

**这两件事是绑定的**，飞书没有"只对 API 公开、不对搜索公开"的开关。

**怎么判断要不要设 public**：

| 知识库内容 | 建议 |
|---|---|
| 学习笔记 / 算法题 / 命令备忘 / 公开知识整理 | ✅ 可设 public（其他飞书用户看到无所谓） |
| 公司业务数据 / 客户名单 / 未发布产品 | ❌ 保留 private（lark-cli 读不到，换方案） |
| 个人读书笔记 / 公开知识整理 | ✅ 可设 |

**额外保护**（设 public 后想降低曝光）：

- 飞书后台 → 知识库 → 设置 → 权限管理 → 删除"组织内所有人"组（如果有）
- 飞书后台 → 知识库 → 设置 → 关掉"允许被搜索"
- 这两个能减少在飞书搜索结果里被搜到，但不能完全消除

**对纯个人学习场景**（你的 hot100 / 命令 那种）：**设 public 基本无风险**。

## 安装

```bash
# 方式 1（推荐）：npx
npx @larksuite/cli@latest install

# 方式 2：源码安装
git clone https://github.com/larksuite/cli.git
cd cli
make install
```

## 首次配置

按顺序跑（每个都看输出）：

```bash
# 1. 配置 app 凭据（输入你的 App ID / Secret）
lark-cli config init

# 2. 浏览器 OAuth 登录
lark-cli auth login --recommend
# 第一次会弹飞书登录页，同意授权

# 3. 验证登录
lark-cli auth status
# 期望：{"ok": true, "identity": "user", ...}
```

## 验证能读你的 wiki

```bash
# 列所有可访问的 wiki spaces
lark-cli wiki +space-list --format json --page-all
# 期望：{"ok": true, "data": {"spaces": [{"name": "...", "space_id": "..."}]}}

# 选感兴趣的 space（拿 space_id），列它下面的所有节点
SPACE_ID=<从上面拿到的 space_id>
lark-cli wiki +node-list --space-id $SPACE_ID --format json --page-all
# 期望：{"ok": true, "data": {"nodes": [{"obj_type": "docx", "obj_token": "...", "title": "..."}]}}

# 拿某篇 docx 文档的真实内容（验证 markdown fetch 通）
OBJ_TOKEN=<从 nodes 拿到的 obj_token>
lark-cli docs +fetch --doc $OBJ_TOKEN --doc-format markdown --scope full
# 期望：{"ok": true, "data": {"document": {"content": "<markdown>..."}}}

# 不确定命令时
lark-cli wiki --help
lark-cli docs --help
```

**期望**：列出的 spaces 里能看到你设为"企业公开"的那个 wiki 名字 + space_id。

## 同步到 RAG

```bash
# 跑我们的脚本（同步全部 spaces）
uv run python scripts/sync_lark_to_rag.py

# 只同步某个 space（多次传 --space 可叠加）
uv run python scripts/sync_lark_to_rag.py --space 7649380001841745096

# 调试（只导出 + 落盘，不上传）
uv run python scripts/sync_lark_to_rag.py --dry-run

# 服务端已有数据时跳过确认
uv run python scripts/sync_lark_to_rag.py --yes
```

脚本会：
1. 调 `lark-cli wiki +space-list` 列所有可访问 spaces
2. 对每个 space 调 `lark-cli wiki +node-list --space-id <sid>` 列节点
3. 对每个 docx 节点调 `lark-cli docs +fetch --doc <obj_token> --doc-format markdown` 拿 markdown
4. 落盘到 `./lark-exports/<space_name>/<space>__<title>__<token8>.md`
5. 串行 POST 到 `http://localhost:8000/api/upload`（每传一个文件，服务端会全量 re-embed）

## obj_type 支持矩阵（v0.5-2 D ext）

| obj_type | 含义 | 支持 | 备注 |
|---|---|---|---|
| `docx` | 飞书文档（新版） | ✅ | 主流程，走 `docs +fetch --doc-format markdown` |
| `sheet` | 飞书表格 | ❌ | v0.5-3 用 `sheets +fetch` 实现 |
| `bitable` | 多维表格 | ❌ | v0.5-3 按需 |
| `mindnote` | 思维笔记 | ❌ | v0.5-3 按需 |
| `slides` | 演示文稿 | ❌ | v0.5-3 按需 |
| `file` | 普通文件 | ❌ | 取决于实际 mime（pdf/image） |

## 常用命令参考

| 命令 | 说明 |
|---|---|
| `lark-cli auth status` | 查当前登录 |
| `lark-cli wiki --help` | 看 wiki 所有子命令 |
| `lark-cli wiki +space-list --format json --page-all` | 列 wiki spaces |
| `lark-cli wiki +node-list --space-id <sid> --format json --page-all` | 列某 space 的节点 |
| `lark-cli docs +fetch --doc <obj_token> --doc-format markdown` | 拿 markdown 内容 |
| `lark-cli docs +fetch --doc <obj_token> --doc-format markdown --scope outline` | 只拿标题大纲 |

## 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| `wiki +space-list` 返空 `spaces` | 个人 wiki 没设"企业公开" | 飞书后台 → wiki 设置 → 权限 → 设为企业公开 |
| `wiki +space-list` 返 `{"ok": false, "error": ...}` | scope 不够 | 重新跑 `lark-cli auth login --recommend`（用 `--recommend` 走推荐 scope） |
| 报"App not found" | `lark-cli config init` 时输错了 App ID | 重新 init |
| `docs +fetch` 返"unknown subcommand `+fetch`" | CLI 版本太老 | 升级：`npm install -g @larksuite/cli` |
| `docs +fetch` 返 `{"ok": false}` | obj_type 不是 docx | 本期只支持 docx；其他 obj_type 见"支持矩阵" |
| `wiki +node-list` 超时 | space 节点 >1000，timeout=60s 不够 | 服务端拉长 / 等 v0.5-3 分页 |
| 上传时 400 "格式不支持" | 文件被落盘成 .pdf / .docx | 脚本只产 .md，理论上不会出现；检查手改 output-dir |

## 待办（v0.5-3+）

- 定时调度（OS cron 或 APScheduler）
- 增量检测（基于文件 mtime）
- Lark CLI 失败 retry（仅网络错）
- 支持 sheet / bitable / slides 等其他 obj_type
- 断点续传（`--skip-existing`）

## 相关

- [larksuite/cli GitHub](https://github.com/larksuite/cli)
- [Lark CLI 官方博客](https://www.larksuite.com/en_us/blog/lark-cli)
- [Lark CLI 文档](https://open.larksuite.com/document/mcp_open_tools/feishu-cli-let-ai-actually-do-your-work-in-feishu)
- 我们的 RAG 代码：`app/api/chat.py`、`app/rag/retriever.py`

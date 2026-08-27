# 飞书内容同步到 RAG

v0.5-2 D ext：用飞书官方 [Lark CLI](https://github.com/larksuite/cli) 把飞书 wiki 导出到本地，再通过 `/upload` 入库。

## 为什么不用 OAuth

飞书自建应用读个人 wiki 受 scope 限制（之前测过 JWT 只有 `auth:user.id:read`）。

Lark CLI 是飞书官方工具，OAuth 在 CLI 端解决（用户浏览器登录），我们只消费导出文件。

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
# 列 wiki spaces
lark-cli wiki +list --format json --page-all
# 或
lark-cli wiki spaces list --format json

# 看实际命令格式（如果上面的命令不对）
lark-cli wiki --help
```

**期望**：列出你设为"企业公开"的那个 wiki 名字 + space_id。

## 同步到 RAG

```bash
# 跑我们的脚本
uv run python scripts/sync_lark_to_rag.py

# 调试（只看不传）
uv run python scripts/sync_lark_to_rag.py --dry-run
```

脚本会：
1. 调 `lark-cli wiki +list` 列 spaces
2. 逐个 export 到 `./lark-exports/<space_name>/`
3. 上传到 `http://localhost:8000/api/upload`

## 常用命令参考

| 命令 | 说明 |
|---|---|
| `lark-cli auth status` | 查当前登录 |
| `lark-cli wiki --help` | 看 wiki 所有子命令 |
| `lark-cli wiki +list` | 列 wiki（带 `+` 是快捷命令）|
| `lark-cli wiki +export <id> --output <dir>` | 导出 wiki（具体命令以 --help 为准）|

## 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| `wiki +list` 返空 | 个人 wiki 没设"企业公开" | 飞书后台 → wiki 设置 → 权限 → 设为企业公开 |
| 报"权限不足" | Lark CLI scope 不够 | 重新跑 `lark-cli auth login --recommend` |
| 报"App not found" | `lark-cli config init` 时输错了 App ID | 重新 init |

## 待办（v0.5-3+）

- 定时调度（OS cron 或 APScheduler）
- 增量检测（基于文件 mtime）
- Lark CLI 失败 retry

## 相关

- [larksuite/cli GitHub](https://github.com/larksuite/cli)
- [Lark CLI 官方博客](https://www.larksuite.com/en_us/blog/lark-cli)
- [Lark CLI 文档](https://open.larksuite.com/document/mcp_open_tools/feishu-cli-let-ai-actually-do-your-work-in-feishu)
- 我们的 RAG 代码：`app/api/chat.py`、`app/rag/retriever.py`

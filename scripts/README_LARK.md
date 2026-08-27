# 飞书内容同步到 RAG

v0.5-2 D ext：用飞书官方 [Lark CLI](https://www.feishu.cn/content/article/7623291503305083853) 把飞书 wiki 导出到本地，再通过 `/upload` 入库。

## 为什么不用 OAuth

飞书 OAuth 自建应用读个人 wiki 受 scope 限制（我们试过，JWT scope 只有 `auth:user.id:read`，不够）。

Lark CLI 是飞书官方绕路：用 CLI 客户端（飞书客户端本身有权限）做 OAuth，然后从命令行导出内容。我们只要消费导出的文件。

## 安装

```bash
# 任选一种
npm install -g @larksuite/cli
# 或
npx skills add larksuite/cli -y -g
```

第一次跑会让你浏览器登录飞书 OAuth。

## 使用

### 准备

1. 我们的 RAG 服务在跑：`uv run python -m app.main`
2. `.env` 配好了 `ZHIPU_API_KEY` 和 `SILICONFLOW_API_KEY`

### 同步全部 wiki

```bash
uv run python scripts/sync_lark_to_rag.py
```

### 只同步特定 space

```bash
uv run python scripts/sync_lark_to_rag.py --space 7423456789 --space 7567abcdef
```

### 调试（只看不传）

```bash
uv run python scripts/sync_lark_to_rag.py --dry-run
# 列出导出的文件，但不调 /upload
```

### 指定服务地址

```bash
uv run python scripts/sync_lark_to_rag.py --service-url http://192.168.1.10:8000
```

## 工作流程

```
Lark CLI（飞书客户端 OAuth）
   ↓ 导出 wiki 到 ./lark-exports/<space_name>/*.md
我们的脚本（sync_lark_to_rag.py）
   ↓ 读文件 → POST /upload
RAG 服务
   ↓ chunk → embed → 入库
```

## 待办（v0.5-3+）

- 定时调度（cron 或 APScheduler）
- 增量检测（基于文件 mtime）
- Lark CLI 命令格式 verify（装了后跑一次就知道实际命令）
- Lark CLI 失败的 retry 逻辑

## 相关

- 飞书 CLI 官方介绍：https://www.feishu.cn/content/article/7623291503305083853
- 我们的 v0.5-2 RAG 代码：`app/api/chat.py`、`app/rag/retriever.py`

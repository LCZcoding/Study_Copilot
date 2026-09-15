"""飞书 wiki → RAG 同步工具包。

模块分层（依赖只能从上往下，叶子模块互不依赖）：

    sync.py        编排层：SyncConfig + run_sync()，同步主流程
    ├── lark_cli.py    Lark CLI 适配器（子进程封装、身份、wiki/docs 命令）
    ├── rag_client.py  RAG 服务 HTTP 适配器（upload / health / delete）
    ├── manifest.py    增量同步账本（内容指纹的读写）
    └── filenames.py   纯函数：飞书标题 → 合法的落盘文件/目录名

入口脚本 scripts/sync_lark_to_rag.py 只负责进程级设置（UTF-8）和
命令行参数解析，业务全部收敛在本包内。
"""

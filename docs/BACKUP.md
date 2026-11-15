# 连接云盘和备份

只有使用者自己的授权才可读取其云盘。源码没有共享 Google OAuth client、账号或仓库口令。

1. 用实例 `bin/rclone config` 创建自己的 Drive remote（例如 `drive`）。按 [rclone 官方 Drive 文档](https://rclone.org/drive/#making-your-own-client-id) 创建自己的 OAuth 客户端；仅备份应用文件可选 drive.file。长期任务需按官方说明处理 Google 应用发布状态，不能依赖 Testing 的短期授权。
2. 将授权配置保存在私有目录，使用全新专用远端路径，显式初始化：

```bash
.venv/bin/python scripts/backup.py init \
  --repository rclone:drive:HaloDeck/my-workspace \
  --rclone-config /YOUR/PRIVATE/rclone.conf
.venv/bin/python scripts/backup.py backup
.venv/bin/python scripts/backup.py kit --output /YOUR/PRIVATE/halodeck-recovery.zip
```

3. 把恢复包保存到原服务器之外（私人密码管理器、私人 Notion 或离线设备）。它含解密钥匙与授权，不是可以公开分享的发布附件。使用 `recover.py --list` 验证授权，再恢复到空目录并验证应用数据。
4. 校验通过后再启用计划：

```bash
.venv/bin/python scripts/backup.py enable
# 暂停自动计划，不删旧版本：
.venv/bin/python scripts/backup.py disable
```

默认每7天一次，失败隔24小时重试，可在实例 settings.json 调整 `backup_interval_hours` 与 `backup_retry_hours`（正整数小时），随后重启本实例。面板的立即备份只提交一个固定任务，不接受命令、路径或任意后端 URL。

## 覆盖范围

默认保存实例 config、apps、private、workspace以及源码；`backup_roots` 可补充项目数据目录。缺少预期目录/数据库时失败，不初始化空远端库，不同步删除历史版本。目录中的新文件会自动收录；符号链接只保存链接本身，不跟随到范围外。虚拟环境、node_modules、构建缓存、下载的工具及实例日志排除。

SQLite 通过在线 backup API 导出并做完整性检查，FileBrowser 暂停后拷贝再恢复运行。其他数据库（例如 PostgreSQL/MySQL）需要独立一致性导出适配，不能把热拷贝文件宣传为可靠恢复。上游应用需要重新登录时应按其正常流程操作。

本机仅保留索引缓存和上传期间的数据库导出；导出在任务结束后清理。restic 去重并保留版本，不是每次上传全量。暂不执行 forget/prune；空间保留策略应在真实恢复验证后另行设定。

完整流程可用显式 `--local-test` 与隔离目录验证，但本地测试必须标为非异地，不能当作云盘联通证据。

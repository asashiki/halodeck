# 将工作站恢复到另一台机器

需要：私人恢复包、可用的存储账号、Linux x86_64、Python3.12+、足够磁盘空间与网络。

解压私人恢复包，进入文件夹：

```bash
python3 recover.py --list
python3 recover.py --target /srv/halodeck-restored
# 或恢复指定快照：
python3 recover.py --snapshot SNAPSHOT_ID --target /srv/another-empty-directory
```

脚本只向全新空目录恢复，拒绝符号链接目标；从官方发行下载固定版本 restic/rclone并校验SHA-256，使用全新临时索引缓存，下载后验证文件，再应用在线数据库导出并检查SQLite。不会启动任何服务、切换域名、删除远端版本或覆盖现役目录。

恢复后的目录保留原绝对路径结构。先读快照 export/manifest.json 核对覆盖范围。数据校验完成后，再由Agent准备系统依赖、Python环境和Node、安装上游工具，把实例和源码放回合适位置；保持HALODECK_HOME一致，重新登记回生，逐个检查应用。

必须确认唯一活动实例：先确认旧机停用，再启动定时写入、模型网关与域名入口。存储授权被撤销时用原OAuth应用重新授权；不要更换仓库解密口令来“修复”登录问题。密钥只存在旧VPS就不算具备整机灾备。

恢复包中的密码、rclone.conf不可提交GitHub或放在公开附件。GitHub项目只含配方，不含个人的数据和钥匙；服务重新上线与数据恢复是不同验收步骤。本项目尚未在真实全新VPS完成端到端部署演练。

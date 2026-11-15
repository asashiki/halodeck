# 部署与启动

实例目录默认是 `~/.local/share/halodeck`，源码目录可放任意位置。所有应用共用一个可信所有者，终端具有该 Linux 用户的权限；不把文件入口的目录限制当成整个桌面的沙箱。

## 端口

`settings.json` 的 `port` 默认为8100。网关是基础端口，Homepage、终端、VS Code、文件、Magpie Web、Magpie API依次使用 +1 到 +6；全部回环监听。选定一组未占用端口后再初始化，不与已有服务争抢端口。

域名写进 `hostname`（只写主机名，不带协议与端口）。网关始终需要独立 Basic 登录；WebSocket、上传与管理 API 也受保护。HTTP 仅用于本机回环或 SSH 转发，公网必须接 HTTPS，并建议叠加 Cloudflare Access 等身份门禁。反向代理要保留 Host、Authorization、Origin、WebSocket Upgrade；不能把后端端口直接暴露。

## 生命周期

```bash
.venv/bin/python integrations/revive/revive.py status halodeck
.venv/bin/python scripts/manage.py stop
.venv/bin/python integrations/revive/revive.py start halodeck
```

每个新服务先登记，后启动。HaloDeck 只有一个 supervisor 管理子进程；子服务异常退出至少间隔15秒再拉起。`paused/应用名` 标记可暂停特定子服务。不要另设第二个进程管理者反复启动同一批应用。

Revive 登记不等于宿主的开机触发器。要自动开机启动，将已登记的 `revive.py start halodeck` 接到宿主现有的用户启动机制；容器平台、systemd与受管开发机的入口不同。本安装器不会擅自改全局 shell、系统启动脚本、防火墙、DNS或其他用户服务。可用 `REVIVE_STATE` 指定独立登记册，也可沿用已有 Revive 核心登记。

## 更新与损坏恢复

先备份并确认可以恢复，再更新源码。`install.py` 使用 binaries.lock.json 的固定发行与校验值；已有工具不会默默升级。`init` 是首次初始化，不是修复命令：预期数据库丢失时先停止、从快照恢复，不能通过 prepare-apps 重建空库后假称原数据已恢复。

源码、Node运行时、Python虚拟环境和上游依赖需要在新机器上重新准备。保留安装路径或修改恢复后的设置与回生登记，不能把旧机器的绝对路径当成新机一定存在。

# Uptime Kuma on Lunes.host

> 在 Lunes.host 上使用 Node.js Generic 方式部署 Uptime Kuma 监控面板

## ✨ 功能特性

- ✅ 每天定时自动备份（默认凌晨 4 点）
- ✅ 自动清理超过 5 天的旧备份
- ✅ 首次启动自动恢复最新备份
- ✅ 支持 ZIP 加密备份（可选）
- ✅ WebDAV 云端存储，数据安全可靠

## 📁 项目结构

```
/home/container/
├── package.json
├── .nvmrc
├── config.sh          # ⚙️ 配置文件（需修改）
├── start.sh           # 🚀 启动脚本（需 755 权限）
└── scripts/
    ├── backup.sh      # 💾 WebDAV 备份（需 755 权限）
    └── restore.sh     # 📥 WebDAV 恢复（需 755 权限）
```

## 🚀 快速开始

### 1️⃣ 查看端口

在 Lunes.host 控制面板查看分配给你的端口号：

![查看端口](./img/0.png)

### 2️⃣ 上传文件并配置权限

**上传项目文件：**

![上传文件](./img/1.png)

**为脚本添加 755 执行权限：**

![添加权限](./img/2.1.png)
![添加权限](./img/2.2.png)

需要添加权限的文件：
- `start.sh`
- `scripts/backup.sh`
- `scripts/restore.sh`

### 3️⃣ 修改配置文件

编辑 `config.sh`，根据你的实际情况修改：

```bash
#!/bin/bash
# ============================================
# Uptime Kuma 配置文件
# ============================================

export PORT="2114"
export TZ="Asia/Shanghai"

# 预构建包下载地址
export KUMA_DOWNLOAD_URL="https://github.com/oyz8/action/releases/download/2.0.2/uptime-kuma-2.0.2.tar.gz"

# ============================================
# WebDAV 备份配置
# ============================================
export WEBDAV_URL="https://zeze.teracloud.jp/dav/backup/Uptime-Kuma/"
export WEBDAV_USER="用户名"
export WEBDAV_PASS="密码"

# 备份密码（可选，留空则不加密）
export BACKUP_PASS=""

# 每天备份时间（小时，0-23）
export BACKUP_HOUR=4

# 保留备份天数
export KEEP_DAYS=5

# ============================================
# 哪吒监控 Agent 配置（可选）
# ============================================
# 从哪吒面板获取以下信息：
# 管理面板 -> 服务器 -> 添加服务器 -> 复制配置

export NZ_SERVER="beck.nyc.mn:443"      # 哪吒服务器地址:端口
export NZ_UUID="fe3d**************ae7"  # Agent UUID
export NZ_CLIENT_SECRET="444*****************444" # 客户端密钥
export NZ_TLS=true                          # 是否启用 TLS (true/false)
# export NZ_AGENT_VERSION="v1.0.0"           # 可选，默认自动获取最新版
```
[也可以预构建自己的包](https://github.com/oyz8/action/blob/main/.github/workflows/Uptime-Kuma.yml)

> 💡 **WebDAV 推荐：** 本项目使用 [InfiniCLOUD (Teracloud)](https://infini-cloud.net/en/) 作为备份存储
> 
> 🎁 注册时输入推荐码 `PPMZC`，可在 20GB 基础上额外获得 5GB 存储空间！

### 4️⃣ 配置启动命令

在 Startup 设置中填入：

```
npm start
```

![启动命令](./img/3.png)

### 5️⃣ 启动服务

点击 **Start** 按钮启动：

![启动](./img/4.png)

## 🛠️ 手动操作(在启动命令改为下面命令)

```bash
# 手动执行备份
bash scripts/backup.sh

# 手动恢复最新备份
bash scripts/restore.sh

# 恢复指定备份文件
bash scripts/restore.sh lunes-host-backup-2024-12-26-10-30-00.zip
```

## ⚠️ 注意事项

- 必须使用 **Node.js Generic** 方式部署
- 首次启动需要下载预构建包，请耐心等待
- 确保 WebDAV 目录已提前创建
- 脚本文件必须有执行权限（755）

## 📝 许可证

MIT License

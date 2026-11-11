# codex-server-bootstrap

把一台全新 Ubuntu 服务器配置成可由本地 Codex 通过 SSH 接入的工作环境。

服务器安装 Mihomo 代理、Codex 完整程序包和订阅管理页面；本机配置 SSH 密钥与连接别名。订阅、网页密码和 ChatGPT 登录由使用者自行完成。

支持 **Ubuntu 24.04 / 26.04，x86_64，systemd**。当前不支持 ARM、容器内缺少 systemd 的环境或其他发行版。

## 最短上手流程

### 1. 在服务器安装

使用云控制台的终端，以 root 登录。只有普通管理员账户时，先执行 sudo -i。
即使系统没有 Git、Python 或 curl，也可以从下面开始：

~~~bash
apt-get update
apt-get install -y git ca-certificates
git clone https://github.com/Jieniljienl/codex-server-bootstrap.git
cd codex-server-bootstrap
bash install.sh
~~~

安装器会自动补齐缺失的基础依赖、下载并验证固定版本 Release、创建普通用户 codexsvc、安装用户服务和 Codex 的 SSH 启动入口。
按照提示设置网页密码，至少 12 个字符。

需要服务器能访问 Ubuntu 软件源和 GitHub。若服务器访问 GitHub 不通，可把仓库和 Release 包从本机上传，再使用后文的离线方式。

### 2. 在 Windows 配置本地连接

在**本机 PowerShell**拉取同一仓库，运行：

~~~powershell
git clone https://github.com/Jieniljienl/codex-server-bootstrap.git
cd codex-server-bootstrap
powershell -NoProfile -ExecutionPolicy Bypass -File .\connect.ps1 -Server YOUR_SERVER_IP
~~~

YOUR_SERVER_IP 替换为服务器地址。按提示输入服务器 root 密码；密码不会写入仓库或配置文件。
脚本准备独立 SSH 密钥，把公钥授权给普通服务用户，并在本机 SSH 配置中添加具体主机别名。
已有密钥可通过 -IdentityFile 指定；私钥只留在本机。
若管理员通过 ubuntu 等普通账户 SSH 登录，可加 -AdminUser ubuntu，授权阶段会使用 sudo。
连接多台服务器时，通过 -HostAlias 指定不同别名，例如 codex-dev 和 codex-build。

连接脚本不会关闭原登录方式，不改服务器 SSH 防火墙，也不向公网开放 Codex app-server。

### 3. 选择代理节点并登录 ChatGPT

使用连接脚本输出的 SSH 别名打开隧道。默认别名和准确命令以终端输出为准，例如：

~~~powershell
ssh -L 8090:127.0.0.1:8090 codex-server
~~~

保持窗口开启，浏览器访问 http://127.0.0.1:8090 。使用刚设置的网页密码登录，导入自己的 Clash/Mihomo 订阅，选择节点并检测出口。
如果本机 8090 被占用，改用 -L 18090:127.0.0.1:8090，并访问 http://127.0.0.1:18090 。

然后在这个 SSH 会话中运行：

~~~bash
cd ~/ai-proxy-hub-1.0.0
bash deploy/codex-login.sh
~~~

根据提示在本机浏览器完成 ChatGPT 设备码授权。账号或工作区需要允许设备码登录。

### 4. 在本地 Codex 添加服务器

在本地 Codex / ChatGPT 桌面应用的 **设置 → 连接** 中添加或启用上述 SSH 主机，然后选择服务器上的项目文件夹。

应用会读取本机 ~/.ssh/config 中的具体 Host 别名，通过 SSH 使用远端普通用户登录 shell 的 codex 命令启动 app-server。本项目安装的入口为 ~/.local/bin/codex，保留完整程序包，透传 app-server 参数，并使用同一代理和授权目录。[OpenAI 官方接入说明](https://learn.chatgpt.com/docs/remote-connections#connect-to-an-ssh-host)

完成后，聊天中的文件操作与命令会在服务器所选项目目录执行。

## 安装范围

| 项目 | 默认值 |
|---|---|
| 服务用户 | codexsvc，无 sudo 权限 |
| 运行目录 | /home/codexsvc/ai-proxy-hub-1.0.0 |
| 网页 | 127.0.0.1:8090 |
| Mihomo HTTP/SOCKS 代理 | 127.0.0.1:17890 |
| 代理控制端口 | 127.0.0.1:19090 |
| 可选本地模型 API | 127.0.0.1:18317 |
| Codex SSH 入口 | /home/codexsvc/.local/bin/codex |

顶层安装器需要 root 权限来准备基础依赖，应用统一在普通服务账户下运行。默认快速接入流程使用 codexsvc。

这里的“VPN”沿用 Mihomo 应用代理方案，为 Codex 和可选 CLIProxyAPI 提供出口。它不提供其他设备拨入的 VPN 服务，不启用 TUN，也不修改系统路由或全局代理。

三个 ai-hub-* systemd 用户服务通过 linger 在退出 SSH 后继续运行。
原套件保留 CLIProxyAPI 和网页模型测试功能；仅使用 Codex 不需要授权 CLIProxyAPI。

## 检查、离线安装与再次运行

只检查系统条件：

~~~bash
bash install.sh --check
~~~

下载本仓库对应 Release 中的 ai-proxy-hub-1.0.0.zip，将其与仓库一并上传后：

~~~bash
bash install.sh --archive /absolute/path/ai-proxy-hub-1.0.0.zip
~~~

完全禁止下载和 apt 操作：

~~~bash
bash install.sh --offline --archive /absolute/path/ai-proxy-hub-1.0.0.zip
~~~

离线模式仍要求服务器已具备系统依赖；缺失时会列出并停止。在线安装补齐系统依赖后，Python 依赖和程序二进制来自固定版本的本地安装包。

更新安装器源码：

~~~bash
git pull --ff-only
bash install.sh
~~~

同一发行版再次运行会保留已有订阅、登录资料和端口配置。未知同名账户、目录或服务会导致脚本停止，避免覆盖。
git pull 更新的是安装器源码；本版本不自动跨版本迁移已有运行数据或升级正在使用的 Codex。

## 固定版本与文件来源

- Mihomo v1.19.32
- Codex rust-v0.163.0，包含完整 Linux 程序包与辅助资源
- CLIProxyAPI v8.0.35（可选模型 API 能力）
- Ubuntu 26.04 的 Python 3.14 补充依赖：MarkupSafe 3.0.4、PyYAML 6.0.3

这是可复用的固定版本部署，并不声称使用最新版。Release 的 SHA-256 记录在 release.json；程序与 Python wheel 的来源和摘要在 payload/deploy/assets-manifest.json、wheels-ubuntu2604/manifest.json。

源码仓库不包含服务器地址、个人路径、订阅、密码、私钥或账号授权。运行数据保存在服务器的运行目录，不应提交回 GitHub。
二进制、完整对应源码归档及上游许可证放在 Release，保留原始文件。[第三方组件说明](payload/THIRD_PARTY.md)

## 验证与排错

本项目验证脚本语法、Python 3.14 离线依赖、归属/摘要检查、SSH 启动入口和本地 PowerShell 调用。CI 不登录任何真实账号。

安装器会在目标服务器检查程序版本、Python 导入和服务状态。服务显示 active 不代表代理节点和 ChatGPT 已可用；以实际出口测试、登录结果和本地 Codex 成功打开远端项目为准。

常见情况：

- **GitHub 下载失败**：使用本机下载与 --archive 上传方式。无代理的全新服务器仍需至少能取得安装器与程序包。
- **本地应用发现不了服务器**：检查 ~/.ssh/config 中是否有具体别名，先手动 ssh 别名；然后重新打开应用连接设置。
- **codex 命令不存在**：重新建立 SSH 连接，再检查 command -v codex。不要在 root 身份下替代普通用户验证。
- **SSH 密钥有口令**：在本机 ssh-agent 中加载密钥，或按客户端提示输入；私钥不要上传服务器。
- **安装中途停止**：保留错误输出，修复缺失依赖或冲突后重跑。不要手动删除 data 或授权文件。

## 开发与打包

payload/ 跟踪运行时网页与部署源码；tools/build_release.py 将经过审查的源码覆盖到干净的基础程序包，验证原始二进制和许可证摘要，再生成 Release 与 release.json。
开发打包需要 Python 3.11 或更新版本；目标服务器不运行打包工具。

~~~bash
python -m unittest discover -s tests -v
python tools/build_release.py --base-archive /path/to/clean/base-runtime.zip
~~~

本仓库由用户维护，非 OpenAI 或 MetaCubeX 官方项目。

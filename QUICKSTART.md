# 快速开始

[返回首页](README.md) · [使用指南](WEB_FRONTEND_GUIDE.md) · [备份与恢复](docs/operations.md)

推荐通过 Docker 在本机运行。当前 Web 工作台面向单机、单用户，可保存自己的多个超星账户。

## 方式一：Docker

安装并启动 Docker Desktop。Linux 用户可使用 Docker Engine 和 Compose 插件。

```bash
git clone https://github.com/Niutopia/chaoxing-fanya.git
cd chaoxing-fanya
docker compose up --build -d
docker compose ps
```

打开 **[http://127.0.0.1:5001](http://127.0.0.1:5001)**。

`web` 显示 healthy 表示服务已启动。`data-init` 是一次性的数据目录初始化服务，显示 `Exited (0)` 属于正常情况。首次构建会下载 Python 和前端依赖。

## 第一次使用

1. **添加并验证账户**。填写账户名称和超星账号，选择密码或 Cookie Header 方式。
2. **保存答题连接**。需要答题时，在全局设置填写基础地址、模型和 API Key，开启连接，点击“保存连接”，再点击“测试连接”。修改并重新保存配置后需要重新测试。
3. **选择课程**。进入账户课程页，读取课程并至少选择一门。空选择不会启动全部课程。
4. **确认参数**。设置 1–2 倍播放速度、1–10 个章节并发，以及未开放章节策略。需要提交测验时，同时开启“启用答题”和“自动提交答案”。
5. **开始学习**。任务页显示完成数量、未完成事项和日志。运行结束后点击“刷新判分”，读取所选课程的测验成绩。

### 答题地址怎么填

填写自己服务的基础地址，例如 `https://api.example.com/v1`，并使用该服务支持的模型名称。项目不会附带可用的 API Key。

本机服务可使用类似 `http://localhost:8849/v1` 的地址。Docker 中请求本机回环地址时，应用会转换为 `host.docker.internal`；远程 HTTPS 地址保持原值。这里的地址都是示例，以自己的设置为准。

“测试连接”用于检查服务与模型是否可用，不代表答题一定正确。只想执行其他任务时，可以关闭“启用答题”；测验会保留未完成状态。

## 停止、重启与更新

```bash
# 停止本机 Web 服务，保留数据
docker compose stop web

# 启动已有服务
docker compose up -d

# 更新代码后重新构建并启动
docker compose up --build -d
```

更新前先停止正在执行的学习任务。服务重启时，原运行任务会标记为中断；重新开始会读取平台进度，历史任务仍保留。

账户、配置、历史和缓存位于 `apple-multi-account-web_chaoxing-data` 命名卷。**不要把 `docker compose down -v` 当作普通更新命令，它会删除数据卷。** 迁移或重装前，请按 [完整步骤](docs/operations.md) 备份。

## 方式二：本地开发

需要 Python 3.13+ 和 Node.js 20.19+（或 Node.js 22.12+）。

macOS / Linux，在项目根目录启动后端：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python app.py
```

另开终端启动前端：

```bash
cd web
npm ci
npm run dev
```

| 服务 | 地址 |
| --- | --- |
| 开发前端 | [http://localhost:3000](http://localhost:3000) |
| 开发后端 | [http://localhost:5000](http://localhost:5000) |
| Docker 完整应用 | [http://127.0.0.1:5001](http://127.0.0.1:5001) |

开发前端会代理 `/api` 请求到后端。不设置 `CHAOXING_DATA_DIR` 时，开发数据存放在项目的 `data/` 中，与 Docker 命名卷相互独立。

Windows 安装 Python 和 Node.js 后，可在项目目录运行 `start.bat`。该脚本启动后端与 Vite 开发服务，打开 3000 端口。便携打包及 CLI 见 [进阶说明](docs/operations.md)。

## 遇到问题

```bash
docker compose ps
docker compose logs --tail=100 web
curl -fsS http://127.0.0.1:5001/api/health
```

- 页面无法访问：先确认 Docker 正常运行，5001 端口未被占用。
- 开发页面没有课程：确认 5000 端口的后端已启动，并在页面验证账户。
- 答题不能启动：保存连接后重新测试，确认账户开启答题且已选课。
- 直播或章节未开放：等待平台开放后重新启动该课程。
- 正确率缺失：点击“刷新判分”；平台尚未批阅时会继续显示待判分。

测试与构建命令见 [验证说明](docs/verification.md)。

<div align="center">

# Niutopia · 学习通助手

**在本机管理课程、运行学习任务，查看进度与真实答题表现。**

面向单机、本地、单用户使用的学习通 Web 工作台。

[![CI](https://img.shields.io/github/actions/workflow/status/Niutopia/chaoxing-fanya/main.yml?label=CI)](https://github.com/Niutopia/chaoxing-fanya/actions/workflows/main.yml) [![Python](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white)](pyproject.toml) [![License](https://img.shields.io/badge/License-GPL--3.0-blue)](LICENSE)

[快速开始](QUICKSTART.md) · [使用指南](WEB_FRONTEND_GUIDE.md) · [功能说明](WEB_FEATURES.md) · [备份与恢复](docs/operations.md) · [问题反馈](https://github.com/Niutopia/chaoxing-fanya/issues)

</div>

---

## 从选课到结果，都能看清楚

新版工作台将账户、课程选择、学习参数和任务结果放在一起。启动后可以查看正在处理的章节、完成数量和失败原因，结束后继续保留任务历史与测验成绩。

| 你要做的事 | 工作台提供的能力 |
| --- | --- |
| 管理自己的账户 | 密码或 Cookie Header 验证，账户与参数保存在本机 |
| 选择学习内容 | 读取、搜索、多选课程，保存每个账户的课程选择 |
| 执行学习任务 | 视频、音频、文档、阅读、直播及章节测验，按平台实际状态处理 |
| 配置自动答题 | 接入兼容 Chat Completions 的服务，设置模型、并发、覆盖率和自动提交 |
| 看懂完成情况 | 分别显示课程、章节和任务点进度，列出未完成事项 |
| 看懂答题表现 | 正确题数 / 已判分题数、测验分数、待判分和未提交明细 |
| 中断后继续 | 停止任务或重启服务后重新开始，读取平台已有进度与作答结果 |

**完成任务、提交答案和答对题目是三个不同的结果。** 页面分别展示这些信息，待批阅题目不计入正确率分母；成绩回退时保留此前记录供对照。

## 几步启动

推荐使用 Docker Desktop，或已经安装 Docker Compose 的环境：

```bash
git clone https://github.com/Niutopia/chaoxing-fanya.git
cd chaoxing-fanya
docker compose up --build -d
```

打开 **[http://127.0.0.1:5001](http://127.0.0.1:5001)**。

首次使用按以下顺序操作：

1. **添加账户**：填写超星账户信息，保存并验证。
2. **配置答题**：需要答题时，在全局设置填写服务地址、模型和 API Key，先保存，再测试连接。
3. **选择课程**：进入账户工作台，至少选择一门课程，确认倍速、并发及自动提交选项。
4. **开始学习**：查看实时进度与日志；运行结束后，可刷新判分读取所选课程的已有成绩。

已有数据保存在 Docker 命名卷中，普通停止、重启和镜像更新都会保留。初次构建需要下载依赖，稍后可用 `docker compose ps` 查看状态。

更多安装方式见 [快速开始](QUICKSTART.md)，Windows 脚本、本地开发和 CLI 也在那里说明。

## 日常使用

| 操作 | 命令或入口 |
| --- | --- |
| 查看服务 | `docker compose ps` |
| 查看最近日志 | `docker compose logs --tail=100 web` |
| 停止服务 | `docker compose stop web` |
| 启动已有服务 | `docker compose up -d` |
| 更新当前代码构建 | `docker compose up --build -d` |
| 检查服务是否响应 | `curl -fsS http://127.0.0.1:5001/api/health` |
| 备份、恢复或迁移 | [完整操作步骤](docs/operations.md) |

**更新前先停止正在执行的学习任务。** 数据库、`secret.key` 和答案缓存需要一并备份；仅复制数据库无法完整迁移账户配置。

## 答题与进度如何计算

- **正确率**：平台判定完全正确的题数 ÷ 已有逐题判定的题数。待判分、未提交和无法读取判分的题目分别显示。
- **题库覆盖率**：查到答案的比例，用于控制保存或提交；它不代表答案正确。
- **提交成功**：平台确认接收表单；判分可能稍后才出现。
- **课程完成**：按实际任务结果统计。未开放章节、未开始的直播和失败任务会保留未完成状态。
- **重新开始**：先检查平台已有完成状态和测验结果；服务重启不会自动恢复旧任务的运行线程。

“刷新判分”只读取成绩，不会重做或提交测验。模型答案、教师批阅和平台开放状态仍会影响最终结果，项目不承诺满分。

## 功能范围

新版 Web 使用共享的 AI 答题连接；其他题库适配器、外部通知和部分 OCR 配置由 CLI 或后端兼容能力提供。具体入口见 [功能对照](FEATURE_COMPARISON.md)。

本地 SQLite 保存账户、偏好、任务历史和判分报告。任务异常时保留原因，测验请求另有可追踪的操作记录。完整流程验收覆盖取消后重跑、提交响应丢失、进程中断恢复和数据备份恢复，参见 [验证说明](docs/verification.md)。

## 开发与文档

后端使用 Python 3.13+ / Flask，前端使用 React / Vite / TailwindCSS。

```text
chaoxing-fanya/
├── app.py              # Web 服务入口
├── main.py             # CLI 与共享学习调度
├── api/                # 平台交互、任务处理、题目解析与答题
├── webapp/             # 账户、任务管理、SQLite 持久化
├── web/                # 新版 Web 界面
├── tests/              # 后端与完整流程测试
├── compose.yaml        # 本机 Docker 部署
└── docs/               # 部署、验证与维护文档
```

| 文档 | 适合什么时候看 |
| --- | --- |
| [快速开始](QUICKSTART.md) | 第一次安装，或搭建本地开发环境 |
| [使用指南](WEB_FRONTEND_GUIDE.md) | 配置账户、答题连接和学习参数 |
| [功能说明](WEB_FEATURES.md) | 确认页面能力、任务状态与统计口径 |
| [部署与备份](docs/operations.md) | 更新、迁移、备份、恢复及 CLI 配置 |
| [验证说明](docs/verification.md) | 运行测试、理解已覆盖的异常场景 |
| [前端开发](web/README.md) | 修改和构建 Web 界面 |

反馈问题时，请附上运行方式、具体操作、预期与实际结果，以及相关错误日志；先移除账户密码、Cookie 和 API Key。

## 致谢与许可

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 和 [sweetcornna/chaoxing-fanya](https://github.com/sweetcornna/chaoxing-fanya) 继续开发，感谢原作者及社区贡献者。

项目使用 [GNU GPL v3](LICENSE)。使用、修改和分发请遵循许可证；平台使用请遵守对应课程与服务规则。

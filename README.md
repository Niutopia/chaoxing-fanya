# 超星学习通自动化刷课工具（Web + CLI 全量可视化）

<p align="center">
  <a href="https://github.com/ymylive/chaoxing-fanya" target="_blank">
    <img src="https://img.shields.io/github/stars/ymylive/chaoxing-fanya" alt="GitHub Stars" />
  </a>
  <a href="https://github.com/ymylive/chaoxing-fanya" target="_blank">
    <img src="https://img.shields.io/github/forks/ymylive/chaoxing-fanya" alt="GitHub Forks" />
  </a>
  <a href="https://github.com/ymylive/chaoxing-fanya" target="_blank">
    <img src="https://img.shields.io/github/license/ymylive/chaoxing-fanya" alt="License" />
  </a>
  <a href="https://github.com/ymylive/chaoxing-fanya" target="_blank">
    <img src="https://img.shields.io/github/languages/code-size/ymylive/chaoxing-fanya" alt="Code Size" />
  </a>
</p>

> 基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 二次开发：补全 Web 可视化、OCR、题库、外部通知与便携打包能力，向原作者致敬。

## 功能亮点

- **可视化覆盖 100% 功能**：React + TailwindCSS 前端，桌面/移动自适应，实时日志与进度
- **一键上手**：Windows `start.bat` 检查依赖后即启动前后端；支持 Docker 与便携打包
- **题库全家桶**：Yanxi / LIKE / TikuAdapter / AI / SiliconFlow，可调覆盖率与自动提交
- **OCR 多方案**：内置 PaddleOCR 或外部大模型（OpenAI / Claude / Qwen / SiliconFlow 等）
- **通知渠道**：Server酱 / Qmsg / Bark / Telegram 等完成 & 错误推送
- **CLI 同步**：命令行模式与 Web 功能一致，便于集成与自动化

## 快速开始（推荐一键）

```bash
git clone --depth=1 https://github.com/ymylive/chaoxing-fanya
cd chaoxing-fanya
start.bat  # Windows 双击或命令行运行
```

启动后浏览器会自动打开 `http://localhost:3000`，按界面提示登录并开始学习。

### 其他运行方式

**手动启动（前后端分开）**
```bash
# 后端
pip install -r requirements.txt
python app.py        # 默认 http://localhost:5000

# 前端（新终端）
cd web
npm ci
npm run dev          # 默认 http://localhost:3000
```

**Docker（本地 Web 部署）**

```text
Web UI:            http://127.0.0.1:5001
Container Web:     0.0.0.0:5000
Host answer API:   http://localhost:8849/v1
Container target:  http://host.docker.internal:8849/v1
Persistent data:   named volume chaoxing-data at /app/data
```

The Web UI keeps the answer-service base URL visible and saved as
`http://localhost:8849/v1`. When the app makes an outbound request from the
container, it translates only that loopback destination to
`http://host.docker.internal:8849/v1`; the user's configured value is never
rewritten in the UI or persisted settings.

Start and inspect the deployment with:

```bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:5001/api/health
docker compose logs --tail=100 web
```

`data-init` 是一次性权限迁移服务：每次启动前，它只对 `/app/data`
执行所有者与权限修复，然后退出。看到它处于 `Exited (0)` 是正常现象；长期运行的
`web` 服务仍以 UID/GID `10001`、只读根文件系统和零 Linux capabilities 运行。
因此，从旧版 root 容器创建的数据卷升级时无需手工 `chown`。

备份 SQLite 时必须先停止写入并完成 WAL checkpoint。下面的命令启用 shell
遇错即停，任何一步失败都不会继续生成一个看似成功但不完整的备份：

```bash
set -euo pipefail
# Stop the writer before touching SQLite.  `docker compose ps -q web` is the
# running-container form; -aq also finds the stopped container for inspection.
docker compose stop web
container_id="$(docker compose ps -aq web)"
if [ -z "$container_id" ]; then
  echo "无法备份：找不到 Compose 服务 web 容器，请先执行 docker compose up -d。" >&2
  exit 1
fi
data_volume="$(docker inspect "$container_id" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')"
if [ -z "$data_volume" ]; then
  echo "无法备份：web 容器没有 /app/data 命名卷。" >&2
  exit 1
fi
# Flush SQLite WAL pages after the application has stopped writing.  The
# temporary Compose container uses the same named volume and is removed after
# the checkpoint.  No database file is copied while a writer is active.
docker compose run --rm --no-deps web python -c "import sqlite3; from pathlib import Path; p=Path('/app/data/chaoxing-web.sqlite3'); c=sqlite3.connect(p) if p.exists() else None; c and c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c and c.close()"
backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/chaoxing-fanya-backup.XXXXXX")"
docker run --rm -v "${data_volume}:/source:ro" -v "$backup_dir:/backup" alpine tar -czf /backup/chaoxing-data-backup.tgz -C /source .
echo "Backup written to $backup_dir/chaoxing-data-backup.tgz"
```

恢复时也要先停服务，避免恢复过程中有进程继续写数据库。下面的示例把备份解压回同一个命名卷；恢复完成后再启动 Web 服务：

```bash
set -euo pipefail
backup_file="/path/to/chaoxing-data-backup.tgz"
[ -f "$backup_file" ] || { echo "备份文件不存在：$backup_file" >&2; exit 1; }
docker compose stop web
container_id="$(docker compose ps -aq web)"
[ -n "$container_id" ] || { echo "找不到 Compose 服务 web 容器" >&2; exit 1; }
data_volume="$(docker inspect "$container_id" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')"
[ -n "$data_volume" ] || { echo "找不到 /app/data 命名卷" >&2; exit 1; }
docker run --rm \
  -v "${data_volume}:/target" \
  -v "$backup_file:/backup.tgz:ro" \
  alpine sh -c 'set -eu; find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf /backup.tgz -C /target'
# The data-init dependency normalizes restored ownership before web starts.
docker compose up -d web
```

备份文件应放在仓库之外，并与 `secret.key` 一起妥善保存；没有这个密钥，数据库中的加密凭据无法恢复。

Enter the answer API key once in Settings using the password-style Replace API
Key field, then save the connection through the Web UI. The key is encrypted
in the local `chaoxing-data` volume and is not placed in `.env`, source files,
the Dockerfile, the Compose file, the image build context, or image layers.
Configure account limits and answer settings in Web Settings; `.env.example`
contains deployment guidance only. The volume preserves
accounts, preferences, and answer-connection state across container restarts.

To stop the Web service without removing its data, run `docker compose stop web`.
To back up the named volume, use the commands above; they write
`chaoxing-data-backup.tgz` under a temporary directory outside the repository.

**便携打包**
```bash
clean_and_build_portable.bat
```
生成 `chaoxing_portable` 目录，免安装 Python 直接分发。

**命令行模式**
```bash
python main.py                               # 交互式运行（按提示输入账号密码）
cp config.ini.example config.ini             # 首次使用时复制配置模板
python main.py -c config.ini                 # 读取指定配置文件运行
python main.py -u 手机号 -p 密码 -l 课程ID1,课程ID2 -a [retry|ask|continue]
```

## 配置要点（config.ini）

- **登录**：支持账号密码或 cookies.txt（同后端配置说明）
- **题库 `[tiku]`**：`provider=Yanxi|Like|TikuAdapter|AI|SiliconFlow`；`cover_rate=0.0-1.0`；`submit=true|false`
- **未开放任务处理 `[common]`**：`notopen_action=retry|ask|continue`（命令行可用 `-a/--notopen-action` 覆盖）
- **通知 `[notify]`**：`provider=ServerChan|Qmsg|Bark|Telegram`，按注释填写 `url` / `token` / `chat_id` 等
- **OCR**：
  - 本地 PaddleOCR：安装 `paddlepaddle`、`paddlex`，并设置 `CHAOXING_ENABLE_OCR=1`
  - 外部大模型（推荐）：
    ```bash
    export CHAOXING_VISION_OCR_PROVIDER=openai
    export CHAOXING_VISION_OCR_KEY="YOUR_VISION_KEY"
    export CHAOXING_VISION_OCR_MODEL=gpt-4o
    # 可选：CHAOXING_VISION_OCR_ENDPOINT, CHAOXING_VISION_OCR_PROMPT
    ```

更多详细说明见 `WEB_FRONTEND_GUIDE.md`、`QUICKSTART.md`、`FEATURE_COMPARISON.md`、`WEB_FEATURES.md`。

## 使用流程

1) 登录：手机号+密码或上传 cookies  
2) 选课：多选或默认全部课程  
3) 配置：倍速、并发、题库、通知、OCR  
4) 开始：一键启动任务，实时查看进度/日志  
5) 监控：完成/异常自动推送（如启用通知）

## 目录速览

```
chaoxing/
├── app.py                      # Flask 后端入口
├── main.py                     # 命令行入口
├── start.bat                   # Windows 一键启动
├── clean_and_build_portable.bat
├── config.ini.example          # 配置模板
├── api/                        # 后端接口
├── web/                        # 前端（React + Vite + TailwindCSS）
└── resource/                   # 静态资源、模型等
```

## 常见问题

- Docker 端口被占用：确认宿主机 `127.0.0.1:5001` 未被占用；容器内 Web 服务监听 `0.0.0.0:5000`
- 依赖安装失败：后端 `pip install -r requirements.txt --force-reinstall`；前端保留 `package-lock.json`，删除 `node_modules` 后在 `web/` 执行 `npm ci`
- Web 页面未自动打开：手动访问 `http://127.0.0.1:5001`，查看 `docker compose ps` 与健康检查输出
- Docker 设置未生效：打开 Web 设置页面确认配置，并检查持久化数据是否挂载到 `/app/data`

## 致谢

- 原项目：[Samueli924/chaoxing](https://github.com/Samueli924/chaoxing)
- 社区贡献者与所有用户

## 许可与声明

- 许可证：GPL-3.0，仅允许在相同许可证下开源免费使用与再分发，禁止闭源商业化及任何盈利行为
- 本项目仅供学习交流，使用者需自行承担法律与合规责任

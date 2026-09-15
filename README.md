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

> 基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 二次开发：补全 Web 核心流程可视化、后端 OCR/题库兼容能力、CLI 外部通知与便携打包能力，向原作者致敬。

## 功能亮点

- **Web 核心流程可视化**：React + TailwindCSS 前端，桌面/移动自适应，实时日志与进度
- **一键上手**：Windows `start.bat` 检查依赖后即启动前后端；支持 Docker 与便携打包
- **题库全家桶**：Yanxi / LIKE / TikuAdapter / AI / SiliconFlow，可调覆盖率与自动提交
- **OCR 多方案（后端兼容）**：内置 PaddleOCR 或外部大模型（OpenAI / Claude / Qwen / SiliconFlow 等）；Web 运行链可读取兼容配置，但当前 Web UI 不提供账户级 OCR 控件
- **通知渠道（CLI）**：Server酱 / Qmsg / Bark / Telegram 等完成 & 错误推送；Web 账户通知配置接口仅为 legacy/deprecated 兼容保留，不参与 Web 执行
- **CLI 与 Web 核心流程**：命令行适合通知和自动化，Web 适合账户、课程与任务监控

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
Docker publish:    127.0.0.1:5001:5000
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

`data-init` 是 Compose `depends_on` 使用的一次性成功依赖：首次创建（或显式重建）
依赖容器时，`docker compose up` 会先运行它，等待它对 `/app/data` 完成所有者与权限
修复并成功退出，然后才启动 `web`。看到它处于 `Exited (0)` 是正常现象；长期运行的
`web` 服务仍以 UID/GID `10001`、只读根文件系统和零 Linux capabilities 运行。因此，
从旧版 root 容器创建的数据卷升级时无需手工 `chown`。`docker compose restart data-init`
可以按需重启已有的 `data-init` 容器；但 `docker compose restart web`、
`docker restart <web>`、`docker compose start web` 以及 `web` 的 `restart: unless-stopped`
自动重启，都不会主动重新运行 `data-init`。

如果恢复了数据卷、升级了需要重新执行的权限迁移，或需要再次验证数据目录，请在
`web` 停止后显式运行一次性命令（不需要先删除任何 `data-init` 容器）：

```bash
docker compose run --rm data-init
docker compose up -d web
```

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

恢复时也要先验证备份，再停止服务，避免恢复过程中有进程继续写数据库。下面的示例
先把备份完整解压到仓库外的临时目录，并验证数据库与密钥都存在；只有验证成功后，
才停止 `web`、制作第二份安全备份，最后替换同一个命名卷。恢复完成后显式运行
`data-init`，再启动 Web 服务：

```bash
set -u -o pipefail
backup_file="/path/to/chaoxing-data-backup.tgz"
if [ ! -f "$backup_file" ]; then
  echo "备份文件不存在：$backup_file" >&2
  exit 1
fi
if ! restore_dir="$(mktemp -d "${TMPDIR:-/tmp}/chaoxing-fanya-restore.XXXXXX")"; then
  echo "无法创建临时恢复目录。" >&2
  exit 1
fi
cleanup_restore() {
  rm -rf -- "$restore_dir"
}
trap cleanup_restore EXIT
if ! mkdir "$restore_dir/extracted"; then
  echo "无法创建临时恢复目录。" >&2
  exit 1
fi
# Validate the gzip stream and tar members before extracting anything.
if ! gzip -t "$backup_file"; then
  echo "备份 gzip 校验失败，停止恢复。" >&2
  exit 1
fi
if ! tar -tzf "$backup_file" >/dev/null; then
  echo "备份 tar 校验失败，停止恢复。" >&2
  exit 1
fi
if ! tar -xzf "$backup_file" -C "$restore_dir/extracted"; then
  echo "备份解压失败，停止恢复。" >&2
  exit 1
fi
if [ ! -f "$restore_dir/extracted/chaoxing-web.sqlite3" ]; then
  echo "备份缺少 chaoxing-web.sqlite3，停止恢复。" >&2
  exit 1
fi
if [ ! -f "$restore_dir/extracted/secret.key" ]; then
  echo "备份缺少 secret.key，停止恢复。" >&2
  exit 1
fi

if ! docker compose stop web; then
  echo "停止 web 服务失败，停止恢复。" >&2
  exit 1
fi
container_id="$(docker compose ps -aq web)"
if [ -z "$container_id" ]; then
  echo "找不到 Compose 服务 web 容器" >&2
  exit 1
fi
if ! data_volume="$(docker inspect "$container_id" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')"; then
  echo "无法读取 /app/data 命名卷。" >&2
  exit 1
fi
if [ -z "$data_volume" ]; then
  echo "找不到 /app/data 命名卷" >&2
  exit 1
fi
if ! safety_dir="$(mktemp -d "${TMPDIR:-/tmp}/chaoxing-fanya-pre-restore.XXXXXX")"; then
  echo "无法创建第二份安全备份目录，原数据未替换。" >&2
  exit 1
fi
if docker run --rm \
  -v "${data_volume}:/source:ro" \
  -v "$safety_dir:/backup" \
  alpine tar -czf /backup/chaoxing-data-pre-restore.tgz -C /source .; then
  :
else
  echo "第二份安全备份创建失败，保留原数据未替换。" >&2
  exit 1
fi
if [ ! -s "$safety_dir/chaoxing-data-pre-restore.tgz" ]; then
  echo "第二份安全备份为空，保留原数据未替换。" >&2
  exit 1
fi
echo "第二份安全备份保存在 $safety_dir/chaoxing-data-pre-restore.tgz"

rollback_original() {
  rollback_failed=0
  if ! docker compose stop web; then
    rollback_failed=1
  fi
  if ! docker run --rm \
    -v "${data_volume}:/target" \
    -v "$safety_dir:/backup:ro" \
    alpine sh -c 'set -u; find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf /backup/chaoxing-data-pre-restore.tgz -C /target'; then
    rollback_failed=1
  fi
  if ! docker compose run --rm data-init; then
    rollback_failed=1
  fi
  if ! docker compose up -d web; then
    rollback_failed=1
  fi
  if ! curl -fsS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:5001/api/health >/dev/null; then
    rollback_failed=1
  fi
  if [ "$rollback_failed" -eq 0 ]; then
    echo "已用第二份安全备份恢复原数据并重新启动 web。" >&2
  else
    echo "回滚未能完整验证，请使用 $safety_dir/chaoxing-data-pre-restore.tgz 手工恢复。" >&2
  fi
}

fail_after_backup() {
  echo "$1" >&2
  rollback_original
  exit 1
}

if docker run --rm \
  -v "${data_volume}:/target" \
  -v "$restore_dir/extracted:/restore:ro" \
  alpine sh -c 'set -u; find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && cp -a /restore/. /target/'; then
  :
else
  fail_after_backup "替换数据失败，已启动回滚。"
fi
if docker compose run --rm data-init; then
  :
else
  fail_after_backup "恢复后的 data-init 失败，已启动回滚。"
fi
if docker compose up -d web; then
  :
else
  fail_after_backup "启动恢复后的 web 失败，已启动回滚。"
fi
if curl -fsS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:5001/api/health >/dev/null; then
  echo "恢复完成，web 健康检查通过。"
else
  fail_after_backup "恢复后的 web 健康检查失败，已启动回滚。"
fi
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
生成 `chaoxing_portable` 目录，免安装 Python 直接分发。便携版按仓库中锁定的
`requirements.txt` 安装基础依赖；由于 PaddleOCR 的 Windows 二进制依赖无法在本仓库
中可靠锁定，便携版明确不包含本地 PaddleOCR、`paddlepaddle` 或 `paddlex`，验证码请
手动输入或配置在线 OCR 服务。

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
- **通知 `[notify]`**（CLI）：`provider=ServerChan|Qmsg|Bark|Telegram`，按注释填写 `url` / `token` / `chat_id` 等。Web 的账户 `notification_config` 仅作为 legacy/deprecated 数据库/API 兼容字段保留，当前 Web 执行路径不可达，也不会自动推送
- **OCR**：
  - Web 运行链仍兼容读取后端保存的 `ocr_config`，用于任务执行；当前 Web UI 没有账户级 OCR 配置控件
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
3) 配置：账户工作台设置倍速、并发等学习参数；在“全局设置”配置共享答题连接和运行限制
4) 开始：一键启动任务，实时查看进度/日志  
5) 监控：在 Web 中查看实时进度和日志；完成/异常通知请使用 CLI 的 `[notify]` 配置

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

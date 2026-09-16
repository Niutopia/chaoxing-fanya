# 部署、备份与进阶配置

[返回首页](../README.md) · [快速开始](../QUICKSTART.md)

日常使用优先阅读快速开始。本页保留完整的数据卷备份与恢复步骤，以及 CLI、OCR 和便携打包说明。备份和恢复脚本使用 Bash，请在项目根目录执行。

## Docker 地址与运行方式

```text
Web UI:            http://127.0.0.1:5001
Docker publish:    127.0.0.1:5001:5000
Container Web:     0.0.0.0:5000
Host answer API:   http://localhost:8849/v1
Container target:  http://host.docker.internal:8849/v1
Persistent data:   named volume chaoxing-data at /app/data
```

以上答题地址仅为宿主机服务示例。请在全局设置（Settings）中保存自己的答题地址、模型和 API Key。容器只在发出请求时把本机回环地址转换为 `host.docker.internal`，页面和数据库保留用户输入的地址。远程 HTTPS 地址按原值使用。

启动并检查服务：

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

答题连接由全局设置页管理，API Key 保存在本地数据卷。先保存连接，再测试已保存的配置。账户、偏好、任务历史、判分报告和答题缓存随数据卷保留。

备份结束后执行 `docker compose up -d web` 恢复服务；日常停止使用 `docker compose stop web`。

## Windows 便携打包

打包脚本会使用 `npm ci` 安装锁定的前端依赖，再构建界面。构建前需要安装 Node.js，并保留 `web/package-lock.json`。

```bash
clean_and_build_portable.bat
```
生成 `chaoxing_portable` 目录，免安装 Python 直接分发。便携版按仓库中锁定的
`requirements.txt` 安装基础依赖；由于 PaddleOCR 的 Windows 二进制依赖无法在本仓库
中可靠锁定，便携版明确不包含本地 PaddleOCR、`paddlepaddle` 或 `paddlex`，验证码请
手动输入或配置在线 OCR 服务。

## 命令行模式
```bash
python main.py                               # 交互式运行（按提示输入账号密码）
cp config.ini.example config.ini             # 首次使用时复制配置模板
python main.py -c config.ini                 # 读取指定配置文件运行
python main.py -c config.ini -l 课程ID1,课程ID2 -a retry
```

### 配置要点（config.ini）

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

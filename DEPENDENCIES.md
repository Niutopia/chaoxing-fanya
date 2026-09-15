# 依赖说明

依赖的唯一可信来源是根目录的 `requirements.txt`、`requirements-dev.txt`
以及 `web/package-lock.json`。不要在启动脚本或文档里单独安装包，否则本机、CI
和 Docker 会得到不同环境。

## 支持的运行环境

- Python 3.13
- Node.js 20.19 或更高的 Node 20 版本
- Docker Compose v2（使用 Docker 部署时）

## 后端

```bash
python -m pip install -r requirements.txt
python -m pip check
```

生产依赖都固定为精确版本。开发和测试环境使用：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

项目不再依赖 `flask-cors` 或 Celery。任务调度由本进程的账户级任务管理器完成，
Docker 因此明确使用单个 Gunicorn worker；不要自行改成多 worker，除非先将任务锁
和状态迁移到共享存储。

PaddleOCR 是可选的本地 OCR 路径，不属于默认 Web/Docker 依赖。只有明确需要本地
OCR 时才按 README 安装，并设置 `CHAOXING_ENABLE_OCR=1`。

## 前端

```bash
cd web
npm ci
npm test -- --run
npm run build
```

必须使用 `npm ci`，这样安装内容严格来自 `package-lock.json`。

## 安全审计

```bash
pip-audit -r requirements.txt
cd web && npm audit --audit-level=moderate
```

CI 会重复安装、测试和构建。更新任何依赖后，都应同时提交对应的 requirements
文件或 `package-lock.json`，并重新执行上述命令。

# 测试与验证

[返回首页](../README.md) · [快速开始](../QUICKSTART.md)

## 运行检查

在项目根目录，使用 Python 3.13+ 安装开发依赖并运行后端测试：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

前端使用 Node.js 20.19+（或 22.12+）：

```bash
cd web
npm ci
npm test
npm run build
```

在项目根目录检查 Docker 配置和构建：

```bash
docker compose config --quiet
docker compose build web
```

GitHub Actions 的 [CI](../.github/workflows/main.yml) 会执行 Linux 后端测试、前端测试与 Docker 构建，并构建 Windows CLI 包。实际运行结果以 [Actions 页面](https://github.com/Niutopia/supernova/actions) 为准。

## 重点覆盖的场景

| 场景 | 验证方式 |
| --- | --- |
| 账户、课程、配置、任务启动 | HTTP 接口与账户会话测试 |
| 取消后模型才返回答案 | 检查取消后没有继续提交或更新已结束任务 |
| 提交已接收，但客户端丢失响应 | 再次启动读取已提交页，检查未产生第二次提交 |
| 章节状态滞后于测验提交 | 通过测验页面重新确认作答状态 |
| 进程被强制结束后重启 | 使用同一隔离目录启动，检查历史、日志和完成数量 |
| 显示字母不同于提交值 | 检查解析和表单提交使用平台实际值 |
| 判分刷新 | 检查只读、不改变运行结果、报告跨重启保留 |
| 备份恢复 | 检查数据库完整性、凭据解密、任务详情和答案缓存 |
| 文档中的恢复步骤 | 检查恢复前验证、第二份备份和失败回滚分支 |

跨进程验收位于 `tests/test_http_lifecycle_acceptance.py`。它运行真实 Web 路由、调度器、题目解析与提交逻辑，外部平台和答案服务使用隔离的合成数据。

## 测试结果如何理解

2026-09-16 的本地完整验收通过 578 项后端测试和 155 项前端测试，前端及 Docker 构建完成；该数字是这次验收的记录，后续新增测试可能变化。

自动化测试验证程序在已覆盖场景中的行为，不证明平台永远可用，也不保证模型每题正确。真实结果仍由课程开放情况、平台响应和教师批阅决定。

诊断日志保留操作编号与课程、章节、测验编号，便于关联请求和结果。数据迁移需同时备份数据库、`secret.key` 和缓存，具体步骤见 [部署与备份](operations.md)。

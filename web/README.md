# Web 前端开发

[项目首页](../README.md) · [使用指南](../WEB_FRONTEND_GUIDE.md) · [快速开始](../QUICKSTART.md)

新版前端包含账户概览、课程工作台、任务详情和全局设置。使用 React 18、React Router、Vite、TailwindCSS、Axios 和 Lucide 图标。

## 开发

需要 Node.js 20.19+（或 22.12+）。先按快速开始在项目根目录启动 Python 后端，再在 `web/` 执行：

```bash
npm ci
npm run dev
```

前端访问 [http://localhost:3000](http://localhost:3000)，`/api` 请求代理到 [http://localhost:5000](http://localhost:5000)。不要把开发端口与 Docker 的 5001 端口混用。

## 测试与构建

```bash
npm test
npm run build
```

产物位于 `web/dist/`。Flask 和 Docker 会提供这个目录中的页面；生产构建可在项目根目录启动 `app.py` 后访问 5000 端口。

`npm run preview` 只用于本地查看构建产物。完整 API 操作请使用后端提供的页面，或带代理的开发服务。

## 目录

```text
src/
├── App.jsx            # 路由与账户/任务共享状态
├── api/               # HTTP 请求封装
├── pages/             # 概览、课程、任务、设置
├── components/        # 账户表单、任务展示、布局与基础组件
└── index.css          # 主题和全局样式
```

交互测试与组件放在一起，以 `.test.jsx` 命名。提交界面改动前检查加载、空数据、失败、取消及完成状态。

课程必须至少选择一门，空选择不会启动全部课程。答题正确率以平台判分为准，具体口径见 [功能说明](../WEB_FEATURES.md)。

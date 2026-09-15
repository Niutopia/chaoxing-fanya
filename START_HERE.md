# 🚀 启动指南

## ✅ 依赖由启动脚本同步

- 后端严格使用 `requirements.txt`
- 前端严格使用 `web/package-lock.json`

---

## 🎯 现在可以启动了！

### 方式一：自动启动（推荐）⭐

**双击运行：**
```
start.bat
```

✨ **智能特性：**
- ✅ 自动同步后端锁定依赖
- ✅ 自动执行 `npm ci` 同步前端依赖
- ✅ 自动启动前后端服务
- ✅ 自动打开浏览器

### 方式二：手动启动

**1. 启动后端（在项目根目录）：**
```bash
python app.py
```
后端将运行在：http://localhost:5000

**2. 启动前端（新开一个终端，进入web目录）：**
```bash
cd web
npm run dev
```
前端将运行在：http://localhost:3000

**3. 打开浏览器访问：**
```
http://localhost:3000
```

---

## 📝 使用步骤

1. **登录** - 输入超星学习通手机号和密码
2. **选课** - 选择要学习的课程
3. **配置** - 设置播放倍速等参数
4. **全局设置**（可选） - 配置所有账户共享的答题连接和运行限制
5. **开始** - 点击"开始学习"按钮
6. **监控** - 查看实时进度和日志；Web 不提供账户级通知/OCR 控件或自动推送

> 说明：后端仍兼容读取账户保存的 `ocr_config`，供 Web 运行链使用；账户
> `notification_config` 仅为 legacy/deprecated 数据库/API 兼容字段保留，当前 Web
> 执行路径不可达。需要完成/异常通知时，请使用 CLI 的 `[notify]` 配置。

---

## ⚠️ 如果遇到问题

### 后端启动失败
```bash
# 重新安装依赖
pip install -r requirements.txt
```

### 前端启动失败
```bash
# 进入web目录
cd web

# 重新安装依赖
npm install
```

### 端口被占用
- 后端默认端口：5000
- 前端默认端口：3000

检查端口占用：
```bash
netstat -ano | findstr :5000
netstat -ano | findstr :3000
```

---

## 📚 完整文档

- [WEB_FRONTEND_GUIDE.md](WEB_FRONTEND_GUIDE.md) - 详细使用指南
- [QUICKSTART.md](QUICKSTART.md) - 快速开始
- [FEATURE_COMPARISON.md](FEATURE_COMPARISON.md) - 功能对照表
- [DEPENDENCIES.md](DEPENDENCIES.md) - 依赖清单（包含所有依赖信息）

---

**现在就开始使用吧！** 🎉

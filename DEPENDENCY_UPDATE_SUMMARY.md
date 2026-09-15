# 依赖更新摘要

当前依赖策略已经统一：Python 生产与开发依赖使用精确版本，前端使用
`package-lock.json` 和 `npm ci`，Docker 与 CI 读取同一组文件。

旧版文档中“单独检查或安装某个 Web 包”和“只要 `node_modules` 存在就跳过安装”
的流程已经移除。`start.bat` 每次都会同步两端依赖，避免代码升级后继续使用旧环境。

具体版本、安装、测试与漏洞审计命令见 `DEPENDENCIES.md`；部署步骤见 `README.md`。

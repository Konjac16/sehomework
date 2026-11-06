# VP Lab 前端

基于官方 [Vue Vben Admin](https://github.com/vbenjs/vue-vben-admin)，MIT License；引入版本提交 `26706d65c9401a7818cf83369081c26b68990ef7`，保留原 LICENSE。使用 `apps/web-antd`（Vue 3 + Ant Design Vue）。其余目录为上游工作区依赖和示例，未加入 VP Lab 路由。

## 启动与构建

普通使用直接启动 `../server.py`，服务优先加载已生成的 `../static/ui`，不需要安装 Node。

修改前端时使用 Node 22.18+ 或 24.12+，以及 package.json 指定的 pnpm：

```sh
cd vp_demo/frontend
pnpm install
python3 scripts/vplab/build.py
pnpm -F @vben/web-antd exec vue-tsc --noEmit
```

构建脚本先从 `../static` 生成业务资源，再构建 Vben，成功后替换 `../static/ui`。Windows 将 `python3` 换成 `python`。也可通过 `VPLAB_PNPM` 指定 pnpm 可执行文件或 `.cjs` 入口。

## 维护边界

- `apps/web-antd/src/router/index.ts`：中文菜单、路由和本地图标。
- `apps/web-antd/src/preferences.ts`：Vben 布局、主题与工具栏。
- `apps/web-antd/src/views/vplab`：接入现有业务模块。Vben 管理导航；提交、源码、复盘等功能在 Shadow DOM 中运行，隔离原 CSS，沿用 Python API。
- `scripts/vplab/theme.css`：业务内容的字号、间距、表格、表单样式。
- `../static/*.js`：业务逻辑的维护源。不要直接修改生成的 `public/workspace` 或 `static/ui`。
- `/duo` 仍是独立训练房页面，由顶部入口打开。

生产环境不加载上游分析脚本、远程字体或远程图标；保持后端现有 CSP。开发模式先执行 prepare.py，再运行 `pnpm -F @vben/web-antd exec vite`，API 代理指向本机 8765。

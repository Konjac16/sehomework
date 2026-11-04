# VP Lab 本地演示版

面向算法竞赛的训练工作台，当前 v0.3 支持在线获取 CF 提交与源码、提交修改对比、代码旁 AI 复盘、个人经验库、画像、概率估算与补题流程。使用 Python 3.10 及以上版本，无第三方运行依赖。

## 启动

双击 `StartDemo.cmd`，或在此目录运行 `python server.py --browser`。访问 http://127.0.0.1:8765 。关闭启动终端即可停止服务；端口被占用时使用 `python server.py --port 8766 --browser`。

重复双击启动器时，如果端口上已运行兼容版本的 VP Lab，会直接打开现有页面，启动器正常退出；不会重复启动服务或中断原有会话。其他程序占用端口或旧版服务不兼容时，仍会提示更换端口或关闭旧服务。

1. 注册本地账号，密码至少 8 位。
2. 在“数据与账号”填写 CF handle 和该账号自己的 API Key / Secret，选择“全部历史提交”，点击“获取提交与源码”。凭证在 [CF API 设置](https://codeforces.com/settings/api) 创建，无需提供 CF 登录密码。
3. 在“提交历史”查看各次提交，点击“与上次对比”。或从“题目与代码 → 详情”进入对比。默认比较当前提交与前一次提交；可自行选择同题任意两次提交，或逐组浏览相邻提交。红色表示删除、绿色表示新增，深色块标出行内字符变化。默认显示改动及前后 3 行上下文，可切换完整代码。
4. 设置参考 Rating，进入概率实验室试算未做过的题目。
5. 添加补题或复习任务，置顶、写笔记并标记完成方式。

注册账号、导入数据、源码、偏好和任务保存在 `.local/vplab.sqlite3`，不进入 Git。不同账号的记录互相隔离。重启后需要重新登录，数据仍保留。绑定 handle 不验证归属；一个本地账号暂只对应一个 CF handle。

如果旧版正在运行，先停止旧服务，再重新双击 `StartDemo.cmd`。源码详情也支持新标签页、复制和下载。

## AI 复盘与个人经验库

1. 在“数据与账号 → AI 分析”配置 DeepSeek API Key，也可以在代码旁的分析窗口配置。服务只在内存中保存各账号的密钥；退出登录或重启后重新填写。密钥不保存在浏览器、数据库或导出文件中。
2. 从“提交历史”打开源码，点击“AI 分析”；或在两份代码对比时点击“AI 分析这次修改”。选择“两次提交”或“首次 AC 全过程”。全过程包含最早记录到第一次 AC 的所有判题结果，排除后续 AC；未观察到 AC 时为阶段复盘。
3. 查看预览范围，可以补充题面、问题，选择提示或完整解释模式，并附带最多 3 条已确认经验（选择列表展示最近更新的 20 条）。打开窗口、预览、重读结果均不会调用模型；只有点击“开始分析”才会向 DeepSeek 发送选定源码、题面与经验，不发送 CF 凭证。
4. 分析按提交逐步说明可观察改动、可能原因与尚未运行的候选验证。点击引用可定位分析时保存的代码快照。服务核对引用的提交、行号与原文；引用核对成功不等于原因已经证明。
5. 将候选“加入待确认”，或在源码旁手动“记录经验”。在“个人经验库”编辑适用场景、规则与检查步骤，再确认入库。支持搜索、状态和来源筛选、收藏、个人练习标记、回收站恢复；同一候选不会重复入库。
6. 在经验来源中点击“回到代码与已保存分析”，读取原始输入快照与结果。后续同步不会覆盖这些快照；快照对比可逐组查看，无需重新调用模型。新分析需要按当前记录重新预览。

模型通过 `ai_provider.py` 接入 [DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion)，默认 `deepseek-flash`、非思考模式、JSON 输出；可在启动前用 `VPLAB_AI_MODEL` 修改模型名，地址固定为官方接口。调用上限为 90 秒，单次输入最多 100000 字符 / 20 次修改，不静默截断全过程。模型输出不完整时标记失败，手动重试，不自动重复付费请求。相同输入的成功结果会复用。每账号最多一个运行任务，全服务最多两个；停止接收结果不保证供应商停止计费。

旧导入文件和旧版同步记录不具有完整历史证明。新版“全部历史提交”成功遍历后才记录覆盖范围；缺少历史或源码时提示片段复盘，需要主动勾选后开始。题面自动获取方式见下文；没有题面时明确限制为代码分析。当前不执行生成用例，不自动确认经验或判定掌握程度，不提供向量检索。源码引用核验失败的候选不能直接入库。

分析快照、结果、经验及修改版本持久化在原 SQLite 数据库，按登录账号隔离；ZIP 导出增加 `analyses.json` 与 `experiences.json`。服务重启会将未完成任务标记为中断，已保存结果和经验仍可查看。运行前建议备份 `.local/vplab.sqlite3`。`design/` 保留早期独立设计稿，实际功能位于主应用的代码查看和经验库页面。

## 原题链接与自动题面

源码查看、代码对比和 AI 侧栏均提供原题链接。打开当前记录的源码或对比页时，自动读取 CF 公开题面；代码立即显示，不等待题面。提取题意、约束、输入输出、样例和备注，保留 LaTeX 原文与样例换行。展示纯文本，不执行原站 HTML；题面包含图片时明确提示，当前不下载图片或进行视觉识别。

公开题面按平台和题号缓存在 SQLite；成功缓存默认使用 7 天，可主动刷新，同题刷新至少间隔 60 秒。失败缓存 60 秒，全局串行访问且间隔至少 2.1 秒，网络超时 18 秒；刷新失败保留已有文字。接口只允许获取当前用户已有记录的题目，URL 由适配器生成，不接受任意网址，不携带 CF 凭证。后续平台可实现 `PlatformAdapter.fetch_statement(record)`。

AI 预览和创建默认使用缓存题面，将来源、原题链接、获取时间和正文保存到不可变分析快照。手动填写题面时明确记为用户补充，优先于自动结果；刷新造成输入变化时需重新预览。CF 返回 403、429、验证页面或登录页时显示失败原因，不冒充获取成功；可以打开原题、稍后重试或手动补充。当前本机对 CF 1900A 的真实网络验证返回 HTTP 403，成功解析路径通过模拟页面测试，尚未在本机验证线上成功抓取。

## 在线源码同步

使用 [CF 官方 API](https://codeforces.com/apiHelp/methods) 的签名 `user.status(includeSources=true)`，每页 200 条，所有请求共用至少 2.1 秒的调用间隔。源码权限由 CF 决定，只支持 API 凭证所属账号的源码；本平台不验证绑定归属，不代表能够用任意人的 Key 读取其他账号源码。

Key / Secret 只进入当前 HTTP 请求和同步线程内存，不写数据库、日志、浏览器存储或任务状态。页面提交后清空输入框。每次同步需要重新输入；任务结束释放引用，但不承诺进程内存的物理擦除。

页面展示进度并允许中止。每页事务写入，失败、中止、服务重启都保留已完成的分页；当前网络调用结束后执行中止。进程重启后同步任务不能继续，需要重新发起，已有提交按编号去重。最多遍历 100000 条；超过上限会明确标记未完成。CF 分页不是快照，同步期间有新提交时建议再同步一次。未返回源码的记录会标注，并且不会清除已有源码。

旧 JSONL 导入保留在折叠的备用入口。原 `E:/codeforces_submissions.jsonl` 只有 AC 样本，无法显示此前 WA 到 AC 的改动链。代码对比仅展示文本差异，不把某次修改自动解释为错误原因；单份代码最多 200000 字符 / 5000 行。

## 已实现与限制

- 注册、登录、退出；密码使用 PBKDF2 加盐哈希；会话放在内存，Cookie 为 HttpOnly / SameSite。
- CF JSONL 和原始提交 JSON 数组导入；支持 `source` / `sourceBase64`。先验证整份文件再事务写入。重复导入按提交编号更新。
- CF 公开资料及最近最多 1000 次提交同步；网络失败不会清除已有记录。此入口不获取源码，不表示完整历史。
- 官方签名源码同步、完整历史分页、进度与中止；支持记录查询、单次源码读取及同题两次提交对比。
- 题目去重、标签及难度分布、标签 × 难度矩阵、UTC 月份提交统计；不把只含 AC 的文件当作完整通过率数据。
- 私有源码查看、复制、保存；ZIP 导出规范化记录及源码。ZIP 内 `records.normalized.json` 是内部规范格式，目前不是文件导入器的输入格式。
- 概率仅为 `sigmoid((referenceRating - problemRating - 100) / 300)` 规则演示，固定两小时 VP 假设，参数人为指定。未经训练和校准，标签及源码不参与计算，无性能承诺。已通过题返回“不适用”，缺少难度或参考 Rating 时不返回概率。
- 补题排序为人工规则；完成状态由用户确认，不能据此伪造 AC 记录。复习任务与首次解题记录分开。
- VP 房间、比赛推荐、真实预测模型、其他 OJ 适配器尚未实现。
- 本版仅绑定回环地址，用于本地课程 Demo；尚未具备公网部署所需的登录限流、账号找回、HTTPS 和生产监控。

## 模块与接口

- `adapters.py`：平台协议、能力声明、CF 数据规范化及同步。
- `domain.py`：纯统计、概率估算、补题排序，可独立替换模型。
- `server.py`：HTTP 接口、账号会话、SQLite 持久化、按用户授权。
- `review_service.py`：分析范围、快照、任务与证据验证、经验库和版本控制。
- `ai_provider.py`：模型供应商边界，结构化请求和错误处理，不执行源码。
- `problem_statement.py`：题面 HTML 提取、固定原站地址、缓存与限流。
- `static/`：无构建依赖的响应式前端。

业务接口：`/api/dashboard`、`/api/problem`、`/api/import`、`/api/sync`、`/api/predict`、`/api/tasks/create`、`/api/tasks/update`、`/api/settings`、`/api/export`。

新增接口（均要求登录，并按当前用户隔离）：

| 方法 / 路径 | 用途 |
| --- | --- |
| POST `/api/sync/source` | JSON：`handle, apiKey, apiSecret, mode`；mode 为 `all` 或 `recent`，返回 202 和任务状态 |
| GET `/api/sync/source/status` | 当前账号的任务进度，不包含凭证 |
| POST `/api/sync/source/cancel` | 中止当前账号的同步任务 |
| GET `/api/submissions?q=&verdict=&source=&page=` | 每页 25 条历史记录；source 为 yes/no/空，列表不返回代码正文 |
| GET `/api/submission?id=&platform=codeforces` | 读取一次提交及源码 |
| GET `/api/diff?before=&after=&full=0` | 同题两次提交的逐行和行内差异；full=1 显示完整上下文 |
| POST `/api/problem/statement` | `platform,problemKey,refresh`；获取或读取公开题面缓存，返回 `statement.status/text/url/textOrigin/fetchedAt/message`；失败状态为 unavailable，有旧内容时为 stale |
| GET / POST `/api/ai/config` | 查询配置状态；`apiKey` 配置内存密钥，`clear:true` 清除。不返回密钥 |
| POST `/api/analyses/preview` | `analysisScope:pair/first_ac`、题号或两次提交 ID、可选题面/问题/经验 ID；返回指纹、范围和阻断原因，不调用模型 |
| POST `/api/analyses/create` | 同预览参数，加 `fingerprint`；片段需 `allowPartial:true`；新任务 202，已有结果复用 200 |
| GET `/api/analysis?id=` | 持久化任务状态、结构化结果和输入范围 |
| GET `/api/analysis/source?id=&submissionId=` | 读取该次分析的不可变源码快照 |
| GET `/api/analysis/diff?id=&before=&after=&full=0` | 比较该次分析的两份源码快照 |
| POST `/api/analyses/cancel` | `id`，停止接收结果，不自动重试 |
| GET `/api/experiences` / `/api/experience?id=` | 分页搜索筛选 / 经验详情 |
| POST `/api/experiences/from-analysis` | `analysisId,candidateKey`，校验引用后幂等收藏为待确认 |
| POST `/api/experiences/create` | 手动记录，`reviewState:pending/confirmed`，可附 `problemKey` |
| POST `/api/experiences/update,confirm,delete,restore` | 分别为四个路径，`id,expectedVersion`；版本冲突返回 409 |

平台唯一键保留 `platform + externalId/problemKey`，原始难度体系保留为 `difficultyScale`。新 OJ 应新增适配器并声明能力；不能把缺失的难度或标签用零值伪造。后续需将当前单 CF 绑定设置迁移为多平台绑定表，并为任务增加平台字段。

## 验证

运行 `python -m unittest discover -s tests -v` 和 `node tests/test_ui.cjs`。测试使用临时数据库，不会修改实际用户记录。在线 API 使用模拟响应验证签名、分页、失败保留和中止，不表示已经用真实凭证完成线上验证。AI 测试模拟供应商响应，覆盖首次 AC 截止、缺失源码、任务取消、结果复用、证据拒绝、快照不可变、账号隔离、经验确认和版本冲突；不代表真实模型分析质量已验收。前端测试检查模板与异步交互逻辑，不替代浏览器视觉验收。

现有导出器作为外部数据输入，本项目没有复制第三方开源仓库代码；平台适配思想参考之前调研的 icpc-workbench，所有界面和实现为本次新建。

# 日常训练：日常练习与专项训练

日常训练页面包含「日常练习」「专项训练」两个可用模式，恢复上次选中的模式。智能推荐是独立模块，具有自己的难度设置、候选池、RAG／AI 工作流和推荐快照，见 [智能推荐设计](intelligent-recommendation.md)。两者只共享题库、公开资料与提交记录；VP 房间保留独立入口。

## 数据与推荐

CodeforcesAdapter.problem_catalog 使用官方 problemset.problems，缓存 24 小时；网络失败且有缓存时保留旧缓存，页面展示缓存时间。只将有 Rating 的编程题放入候选，不假定 Gym 题目都具备难度数据。平台题目采用 platform + problemKey 标识。题库获取和提交同步通过适配器，选题与训练状态由 TrainingService 处理。

先按难度、已 AC、是否包含尝试过的题和当天冷却过滤。无候选时只显示“当前条件下暂无可推荐题目”，不自动放宽条件。默认不包含尝试过的题；尝试过包括历史 CF 提交和本站已开始的训练。历史覆盖仅限已同步数据，页面保留范围提示。

每道不同题计一次，多标签平分贡献。长期统计使用已同步的提交题目与已开始训练的并集；近期统计使用最近开始/恢复的 10 道不同训练题。标签权重为 `1 + 0.5*(1 - 长期计数/最高长期计数) - 0.3*近期占比`，无长期数据时加分为零。先按权重抽取有候选的标签，再在该标签内等概率抽一道。标签不作为能力结论，也不返回前端。没有标签的候选单独归为无标签组。

## 专项训练：已确认规则

本轮从此前的讨论记录中恢复约定。较早的「共用 Rating、合并三题评估」提案已被用户「两个还是独立吧，两个 rating」覆盖，以下使用最终版本。

- 用户选择 1～3 个不同方向，选择上限为 3。允许显示用户选择的方向，不显示具体题目标签或抽中了哪个方向。
- 候选题命中任一所选方向即可，不要求同时命中全部方向。
- 在有合格候选的所选方向之间等概率抽一个，再在该方向内等概率抽一道题；不叠加日常练习的长期／近期方向权重，不因某方向题目多而提高该方向概率。
- 没题的方向暂时不参与抽取，不扩展到未选方向。所有所选方向都无候选时只显示暂无推荐。新账号没有预选方向，需先选择后应用。
- 默认／人工难度、强度、是否包含尝试题以及未开始的推荐分别保存；切换可恢复各模式设置。
- 两套训练参考 Rating 独立维护。专项内部只有一个 Rating，不按方向继续拆分。
- 日常与专项共用已做题、尝试记录、当天冷却、待反馈队列以及一道进行中的题。切换只改变页面模式，不自动跳过或新建进行中的训练。

## 难度与参考水平

两种模式首次有 CF Rating 则分别初始化为该值，否则分别为 1800；专项不继承日常模式已经动态调整的数值。默认区间：轻松 `[参考-600,参考-200]`、正常 `[参考-200,参考+200]`、挑战 `[参考+200,参考+600]`。人工上下限固定，用户可以设置非 400 跨度。默认正常档，不包含尝试过的题；持久化用户设置。

每个模式分别累计三道已 AC 并反馈的题评估一次，不能用两道日常加一道专项凑成一批：至少两道独立完成且题目难度不低于各自开始时参考水平则 +100；至少两道参考题解且难度不高于各自开始时参考水平则 -100；否则不变。参考提示中性。每批只评估一次，内部参考水平限制为 0～5000。

同步到不同的官方 Rating 时同时覆盖两套训练参考水平，并分别开启新的评估周期，旧周期中尚未反馈或尚未完成的题不计入新周期。官方 Rating 不变时不反复覆盖训练调整。人工难度不变，默认难度跟随参考水平。

## 状态

推荐持久化，刷新不会重新抽题。展示或浏览推荐不建立训练记录。开始训练要求绑定 CF handle，并记录时间和参考水平；每个用户在两种模式合计最多一道 active 题。

`推荐 → active → pending_feedback → completed`

`active → skipped → active`，或后续同步到 AC 后 `skipped → pending_feedback`。

只有已同步的 CF OK 且提交时间不早于训练首次开始时间才可进入 pending_feedback。反馈必须为 independent/hint/editorial，没有手动完成入口；AC 后不能跳过反馈。当前训练完成后提交反馈才生成下一题。以前跳过的题后来 AC 会进入待反馈，不中断另一道 active 训练；空闲时先处理待反馈，再获取新推荐。

跳过原因可空；不直接调整水平。被换掉或跳过的题按 Asia/Shanghai 自然日冷却，次日恢复资格；跳过的题仍受 includeAttempted 控制。主动从历史恢复训练不受推荐冷却和开关限制。每个用户每题保留一条训练记录，重复恢复不重复计数。实现上，训练归属取首次开始时的模式；跨模式恢复旧题不重写归属、开始时 Rating 或评估周期。无论在哪个模式页填写完成反馈，都只计入该题所属模式。页面显示归属，避免错调另一套 Rating。

训练页有 active/skipped 记录时每分钟尝试公开提交同步，也可手动刷新；最近 30 秒已有同步则只核对本地记录。当前适配器每次读取最近最多 1000 次提交，不声称全历史覆盖；完整历史可从已有源码同步入口获取。同步不会调用模型，也不会清空已有源码。

## 接口

所有接口要求登录、按用户隔离；沿用项目现有的 POST 写操作约定。

| 接口 | 输入 / 作用 |
| --- | --- |
| GET /api/training | 状态、偏好、参考水平、当前推荐、active、pending、历史；可选 `?mode=daily/focused`，不传则使用记住的模式 |
| POST /api/training/mode | `mode=daily/focused`；保存页面选择，不跳过当前题 |
| GET /api/training/preferences | 同上，偏好读入口 |
| POST /api/training/preferences | mode(daily/focused), difficultyMode(default/manual), intensity(easy/normal/challenge), minRating, maxRating, includeAttempted；专项另带 directions（1～3 个题库方向）；仅清除该模式未开始的推荐 |
| POST /api/recommendations | mode=daily/focused, platforms=[codeforces]；返回持久化单题推荐；有进行中/待反馈记录时不生成新题 |
| POST /api/training/start | platform, problemKey；只能开始当前推荐 |
| POST /api/training/dismiss | platform, problemKey, reason(可空)；未开始的换题 |
| POST /api/training/skip | id, reason(可空)；已开始的跳过 |
| POST /api/training/resume | id；恢复已跳过记录 |
| POST /api/training/feedback | id, completionMode(independent/hint/editorial)；必填、只接收一次 |
| POST /api/training/refresh | 可带 mode；同步绑定 handle 的最新提交和资料，返回该模式状态 |

训练操作可带 mode，默认取记住的页面模式；反馈以训练记录的归属为准，不能通过传 mode 更改归属。状态返回 mode、两套 ratings；每条训练记录返回所属 mode。专项状态中的 directionOptions 仅为可选训练方向，preferences.directions 为用户选择，题目对象仍剔除全部标签。题目对象含 platform、externalId、problemKey、title、rating、url。reason 值为 too_hard/too_easy/already_done/not_now/空，仅作记录。

## 数据兼容

保留原有 `training_profiles`、`training_recommendations` 保存日常数据，新增 `training_focused_profiles`、`training_focused_recommendations` 保存专项数据，`training_mode_selection` 保存用户最后使用的模式。`training_runs` 只新增 mode 列，已有记录默认 daily；ID、状态、反馈、开始时间和参考值不变。共享训练与冷却表，不复制旧记录。迁移可重复执行。

## 验证

tests/test_training.py 使用临时数据库验证选题、去重、冷却、状态转换、跨账号隔离、AC 校验、三题调整、官方覆盖、缓存失败和并发开始。测试不调用真实 CF，也不改实际账号。前端另外测试标签隐藏、必填反馈、异步响应和退出后的隔离。

`tests/test_focused_training.py` 验证方向限制与 OR 匹配、方向等机会、范围不放宽、独立设置与周期、官方 Rating 双覆盖、共享 AC／冷却、跨模式反馈归属、并发开始及旧库迁移。`node tests/focused_browser.cjs` 用临时数据库验证真实 HTTP／页面交互、模式恢复、三方向上限、手机布局、AC 归属和退出隔离；不访问真实 OJ，不调用模型。

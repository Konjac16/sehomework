# VP 离线赛库 · 2026-11-05

比赛名、时长、题名和场内题序来自 Codeforces： [比赛目录](https://codeforces.com/api/contest.list?gym=false)、[默认题库](https://codeforces.com/api/problemset.problems)、[官方榜单样例](https://codeforces.com/api/contest.standings?contestId=1985)。题目作者与最新图示、交互规则、勘误见每道题的 CF 原题链接。

选场使用本地归档的 `vp_ready_contests` 视图：已结束的 CF/ICPC 编程赛，时长与题号受房间支持；官方榜核对过场内题序，每个题位都找到题面正文和至少一组公开样例记录。2026-11-05 版本包含 1,996 场、12,538 个赛内题位。公开样例的存在不表示自动判题，也不保证题图、交互对话、换行与原页完全一致。

题面正文、输入输出格式及公开样例依照匹配的发布版本保留；每题分别记录正文、样例的提供方：

- `openr1`：[Open-R1 / Codeforces，固定版本](https://huggingface.co/datasets/open-r1/codeforces/tree/fbe3f6e903ee854eec2e69e9d96d0306cde59baf)，发布说明标注 CC BY 4.0。
- `ronin`：[Ronin5205 / Codeforces-Problemset-Statements，固定版本](https://github.com/Ronin5205/Codeforces-Problemset-Statements/tree/cdbad90365bba6ddba3fd28dc25b93fe8c596b23)，附原仓库授权文件 Ronin-LICENSE.txt。部分样例的换行在来源中已丢失，界面提示核对原题。
- `deepmind`：[DeepMind / CodeContests，固定版本](https://huggingface.co/datasets/deepmind/code_contests/tree/802411c3010cb00d1b05bad57ca77365a3c699d6)，公开题面与样例；发布说明标注 CC BY 4.0。
- `icpc`：来自赛事原版题册及公开样例，包括 [ICPC Asia Pacific](https://github.com/icpc-apac-org/icpc-apc-problem-archive)、[NEERC](https://neerc.ifmo.ru/past/index.html) 等。逐题页码、文件原始地址及哈希见完整归档的 `booklet_pages` 和 `statements/icpc/files-manifest.json`。

本包将赛内别名与官方题位对齐，按场划分正文与样例；不含选手提交、官方排名、源码、隐藏测试或用户账号。无需解压，应用按场读取；重新生成与完整数据交接见项目 `tools/build_vp_pool.py` 和 `docs/CF公开数据交接.md`。

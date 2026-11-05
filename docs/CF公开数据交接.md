# 离线赛库的读取和更新

`vp_demo/data/vp-ready.zip` 是 2026-11-05 的固定快照。打开包内的 `catalog.json` 可读取场名、时长和有序题号，`statements/<contestId>.json` 存放该场题面、公开样例和逐题来源。题号是字符串，`01` 和 `1` 不能互换。使用赛库不需要 SQLite、账号或网络。

**哪些比赛会进入包？** 快照中的比赛必须已经结束，且属于支持的 CF 或 ICPC 编程赛。题序按官方 `contest.standings` 核对，因为共享题可能在默认题库中归到另一场。每个赛内题位都必须有正文和至少一组公开样例。当前包包含 1,996 场、12,538 个题位和 23,880 组样例；这不是官方全部比赛的数量。原题链接可用来核对题图和交互说明。包里没有正式榜单、选手源码或隐藏测试。

**如何复核？** [逐来源署名及版本](../vp_demo/data/VP赛库来源.md)与[核验记录](../vp_demo/data/vp-ready.manifest.json)随包保留。仅有 Git 检出时可用 PowerShell 核对文件 SHA-256：

```powershell
(Get-FileHash vp_demo/data/vp-ready.zip -Algorithm SHA256).Hash
```

持有对应的源归档时，可以进一步逐场核对题序、正文和样例。下面的路径换成该归档的实际位置；验证只读打开数据库。

```powershell
python -X utf8 tools/verify_vp_pool.py --db <源归档目录>/catalog.sqlite3
python -X utf8 tools/query_cf_archive.py --archive <源归档目录> contest 2274
```

重新生成时，先由 `fetch_cf_catalog.py`、`fetch_cf_standings.py` 取得公开目录和官方题序，再按来源取得题面、用 `build_cf_archive.py` 建立索引。各工具的 `--help` 列出快照及输出目录；从 Parquet 和 PDF 提取原文的步骤分别需要 PyArrow 和 pdfplumber。下面的命令从已有的完整归档生成一份新包，原归档还须保留 `statements/ronin/LICENSE`：

```powershell
python -X utf8 tools/build_vp_pool.py --db <源归档目录>/catalog.sqlite3 --out vp-ready.new.zip
python -X utf8 tools/verify_vp_pool.py --db <源归档目录>/catalog.sqlite3 --pool vp-ready.new.zip
```

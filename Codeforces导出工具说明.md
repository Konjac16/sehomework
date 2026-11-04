# Codeforces 提交导出工具

这是一个在账号所有者电脑上运行的单文件小工具。它使用 Codeforces 官方 API 导出该账号自己的提交记录，不会把 API 密钥写入导出文件，也不会把密钥发给本工具的作者。

## 使用前准备

1. 账号所有者登录自己的 Codeforces 账号，打开 <https://codeforces.com/settings/api> 创建 API Key 和 Secret。
2. 安装带 Tk 支持的 Python 3.10 或更新版本。无需安装额外 Python 包。
3. 双击 `cf_export_gui.pyw`。如果系统没有关联 `.pyw` 文件，可在命令行运行 `pythonw .\cf_export_gui.pyw`。
4. 输入账号 Handle、该账号自己的 API Key 和 Secret，选择导出文件，然后点击“开始导出”。
5. 默认只保存 Accepted 提交；需要全部提交时，取消勾选“只导出 Accepted 提交”。

API Key/Secret 只在运行期间保存在本机进程内存中。不要把登录密码或 API Secret 发给其他人。API 每 2 秒最多请求一次，导出时间会随提交数量增加。

JSONL 每行包含一条提交记录。工具会把 API 的 Base64 源码字段解码成可读的 `source` 文本；题目对象会包含名称、编号、标签和可用的 rating。提交 API 不返回完整题面正文。

如果 API 没有返回源码，工具会保留提交元数据并在完成时提示。它不会尝试绕过 Codeforces 的访问限制。

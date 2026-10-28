# 源码与修改说明

XiaoA Core 1.2.0，2026-10-28，公开分发的独立内核预览版本。不是 Google 或微信官方发布的软件。

**风险声明：当前 AGY 接入不受消费计划支持，可能导致 Antigravity 账号暂停或终止。个人自用、低频或不公开发布均不构成安全保证。本项目不承诺免封号、无限额度或服务持续可用。官方说明：https://antigravity.google/docs/faq 。**

1.1.0 增加 SQLite 聊天归档、限量相关召回、记忆编号与纠正/遗忘/有效期，以及消息ID去重。AGY接入的官方服务使用限制已在README中明确列出。

1.0.1 增加限于内核版安装目录的卸载入口，支持保留数据、删除数据和取消，并在 Windows 当前用户的应用列表登记卸载入口。

本分发包中的小A源码及适配代码以 GNU Affero General Public License v3.0（AGPL-3.0）分发，完整许可见 LICENSE。完整可修改源码随 app/ 提供，安装与运行脚本也直接提供源码。版本中不内嵌作者身份、账号或私有数据。

主动聊天部分实际适配了 Pancakes-Labs/astrbot_plugin_proactive_chat 的免打扰判断、随机调度和未回复次数门控。上游版权归 Pancakes-Labs 及相应贡献者，固定提交：

https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/tree/77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5

各文件的来源及适配范围见 app/xiaoa_proactive/NOTICE.md。工作时段处理、SQLite 状态、限额、发送取消和宿主接口为小A适配代码。

消息ID提取和去重逻辑适配自 dorokuma/wechatbridge 的 main.py，提交 ccb0770（MIT）。新增账号/用户范围、锁、时间限制和完成记录；完整许可见 app/THIRD_PARTY_WECHATBRIDGE_LICENSE.txt。

此公开版保留经优化的单线程消息队列、原子文件写入、模型超时与异常处理、识图和联网；移除了依赖创作者本机环境的投资脚本、远程控制、表情包/语音/绘图，以及私人作息配置。默认人设重新编写，所有新用户数据首次使用时创建；二维码生成改为本地完成。

运行依赖通过 requirements.txt 从官方 PyPI 安装，Python 和 Antigravity CLI 由使用者电脑单独安装。依赖和官方工具保留各自许可，不因本包而改为 AGPL。

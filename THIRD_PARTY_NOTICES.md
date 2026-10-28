# 第三方来源

本版完整源码与安装脚本均随包提供。整体分发沿用 AGPL-3.0，根目录 LICENSE 保留完整许可。

## 主动聊天策略

- 项目：https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat
- 固定提交：77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5
- 上游许可：AGPL-3.0
- 上游版权：Pancakes-Labs 及相应贡献者
- 实际适配：免打扰判断、随机触发计算、未回复次数门控。
- 适配文件：`app/xiaoa_proactive/policy.py`。
- 完整许可及逐项说明：`app/xiaoa_proactive/LICENSE`、`app/xiaoa_proactive/NOTICE.md`。

SQLite 状态、发送确认、工作时段和宿主回调为小A适配代码，没有宣称运行完整上游框架。

## 入站消息去重

- 项目：https://github.com/dorokuma/wechatbridge
- 固定提交：ccb0770
- 上游许可：MIT
- 上游版权：2026 WeChatBridge contributors
- 实际适配：`main.py` 的消息ID选择和已见消息判定逻辑。
- 适配文件：`app/message_dedup.py`。
- 增加：账号/用户范围、锁、时间上限、哈希标识及完成记录接口。
- 完整许可：`app/THIRD_PARTY_WECHATBRIDGE_LICENSE.txt`。

WeClaude、cc-connect 和 MaiBot 曾用于方案评估；本版未声称整体移植它们，没有复制未列明授权的模块。

## 安装时获取的依赖

Python、AGY 和 requirements.txt 中的第三方依赖分别保留各自许可与服务条件。本包不把这些外部工具的登录状态或安装文件重新分发；源码许可也不会授权使用外部模型服务。

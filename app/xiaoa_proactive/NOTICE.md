# 源码来源与修改说明

本模块采用上游的 GNU Affero General Public License v3.0，完整许可见 LICENSE。
上游代码版权属于 Pancakes-Labs 及相应贡献者。

- 项目：https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat
- 固定提交：77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5
- 源码快照：https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/tree/77a1d408ccd003a9d34ad4f51acfb8fc17bc35f5

## 实际移植的代码

`policy.py` 中的三段逻辑来自以下上游文件，而非仅借用项目名称：

1. `utils/time_utils.py:is_quiet_time`：同日和跨午夜免打扰判断。修改为注入当前时间；非法配置抛错而非放行。
2. `core/services/scheduler_service.py:SchedulerService.compute_next_trigger`：最小/最大间隔、随机间隔及下次触发时间计算。移除 AstrBot 容器依赖，注入时间、随机数和时区。
3. `core/features/gating_feature.py:GatingStage.handle`：未回复次数上限判断，提取为纯函数。

测试保留了上游时间工具和调度方法的源码片段，逐小时核对免打扰判断，并核对随机调度结果。

## 小A适配代码

`engine.py` 是围绕上述上游策略增加的宿主适配：SQLite 状态、每日额度、新输入取消、发送确认、重启去重，以及模型/历史/发送回调。
0.1.1 增加的工作时段限制及工作时段内随机顺延是小A适配代码，未声称来自上游。
小A的 `assistant_hub.py` 与 `clawbot_service.py` 接口修改也随交付包提供。

本模块没有把整个原插件直接运行起来，也没有移植其 WebUI、TTS、遥测或远程通知功能。它是基于明确来源的代码级适配版；不是原作者发布的官方版本。

# 私有资料 JSON

这是供 agent 阅读的建议格式，不是脚本强制解析的统一简历 schema。现有脚本校验由 agent 根据真实页面生成的计划与快照；不会自动把此 JSON 映射到任意网站。

把 `examples/autofill-profile.example.json` 复制到工作目录的 `private/autofill-profile.json`，将虚构记录替换为自己的已确认资料，并设置 `example_only: false`。也可直接告诉 agent 文件的绝对路径，或设置 `JOB_APPLICATION_PROFILE`。不要在此公开仓库保存真人资料。

各经历保留稳定 `record_id`、事实来源 `source`、真实日期精度与可直接使用的正文。建议分为 `education`、`internships`、`projects`、`research`、`competitions`、`awards`、`campus` 和 `skills`；有其他类别时继续完整盘点。论文作者顺序、投稿状态等按来源逐条填写。

`null` 和空数组默认仅表示尚未提供。通过 `field_metadata` 标明 `unknown`、`confirmed_none`、`not_applicable`、`withheld` 或 `pending_confirmation`；这些是建议词汇，已有资料使用其他明确语义时按其定义处理。

`autofill_policy` 与 `presentation_policy` 是呈现偏好，不能充当当前网站的填写、保存或提交授权。运行中的页面快照、计划和状态也可能含个人数据，全部放在私有目录。证件号码、密码与会话凭据不放入示例、运行证据或日志。

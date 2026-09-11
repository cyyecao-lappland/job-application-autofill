# 核验格式

首次网页写入前使用 [execution-contract.md](execution-contract.md) 的 runId、执行方式、当前批次 probe、类别盘点、字段旧值及方法证据，并运行 `preflight.py`。planReady 是计划结构通过，ready 是本批记录的执行条件满足；两者都不表示执行已发生。direct-tool 无需本地 batch.js。本页示例只演示数据结构。

完整的新计划另有 `jd`、`inventory`、`coverage`。例如：

```json
{
  "jd":{"source":"用户提供的官方职位页面","requirements":[{"id":"R1","text":"负责检索算法开发","kind":"core"}]},
  "inventory":[{"id":"E1","name":"示例检索项目","category":"project","source":"career-library/projects/example.md"}],
  "coverage":[{"id":"E1","priority":"P0","requirementIds":["R1"],"decision":"include","operationIds":["F1"],"reason":"直接证明检索算法开发经验"}],
  "operations":[{"id":"F1","label":"项目描述","value":"来源中确认的个人贡献与成果","source":"career-library/projects/example.md","kind":"text","action":"fill"}]
}
```

`operations` 和 `records[].operations` 的 id 全局唯一。`coverage.operationIds` 可引用 `fill` 或 `keep`，其他状态不算已呈现。合并用 `mergeInto` 指向另一 inventory id，并给出实际操作去向。基本信息可标 `mandatory: true`；其他 P0/P1 必须关联 JD 编号。每项 decision/reason 和来源都须齐备。`audit_coverage.py` 检查这些结构关系，不代替事实核实和相关性判断。旧计划没有 inventory 时仍可仅用 `verify_form.py` 做历史保存核验，但不能算新流程的覆盖检查已通过。

快照的 `fields` 数组每项：

```json
{"label":"学校名称","recordIndex":3,"value":"示例大学","visible":true,"required":true}
```

不同记录必须有不同 `recordIndex`，不能用可能重复的 HTML id。敏感证件字段先排除再读值，仅留 `{"label":"证件号码","excluded":true}`。文本用 `value` 记录实际输入值；下拉控件必须用 `selectedValue` 记录已关闭控件的真实选中项，不能从 `display`、候选菜单或搜索框推断。单选可用 `value` 或 `radio` 记录已选项。部分旧 `controls` 格式仍兼容；缺少明确值或 `readStatus` 为 unknown/redacted/unavailable/conflict 时视为未验证，不当作空值。

计划：

```json
{
  "operations": [
    {"label":"成绩(GPA)","anchor":{"label":"学校名称","value":"示例大学"},"kind":"text","before":"","value":"3.4","source":"用户当前回复","action":"fill"}
  ],
  "records": [
    {"anchorLabel":"项目名称","name":"示例项目","operations":[
      {"label":"项目名称","value":"示例项目","source":"履历库具体条目"}
    ]}
  ],
  "manual": [
    {"record":"示例任职","label":"开始时间","value":"2024-09","reason":"资料只有年月，不补造具体日；本站日期控件未验证可靠"}
  ]
}
```

无锚点仅适用于全页唯一标签。`records` 展开为带完整锚点的核验目标。`action` 为 `keep`、`manual`、`missing-fact` 不计填写目标；不存在的选填字段也不能当成功。

`--evidence page` 是当前页面匹配；`reloaded` 必须是保存成功后重开的快照，并包含下面的 capture 记录，否则自动降为 page。工具编号来自真实返回；离线核验器只检查记录齐备，不能独立证明网站保存。

```json
{"capture":{"stage":"after-reopen","readCallId":"真实读取调用编号","reopenCallId":"真实重开调用编号","saveReceipt":{"status":"saved","callId":"真实保存调用编号","indicator":"实际观察到的成功提示或保存卡片"}}}
```

核验器不执行保存、不验证事实、不宣称投递成功。它拒绝证件号计划，并报告缺失、歧义和不匹配，不取第一条记录。导出前须已完成隐私排除，离线脚本不能补救已泄露的 HTML。

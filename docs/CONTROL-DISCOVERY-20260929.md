# 同一浏览器控件扫描与落盘（2026-09-29）

识别对象为控件。页面、栏目只作为定位和记录归属上下文。

- 分支：`feat/control-capability-contract`。扫描同一 Edge 的 22 个现有标签页；其中 13 个是网申/简历页。
- 检查顺丰 5 个、OPPO 11 个编辑视图；只展开、读取、取消本次新开的编辑区，未保存或提交。
- 最终静态回读：440 个可见控件，423 个可唯一匹配注册驱动，17 个已识别类型但尚无驱动。该分母包含非网申页面的搜索和筛选控件，不能当网申完成率。
- 13 组现场菜单开关检查中 11 组通过；另 2 组识别出层级/地区变体。这里只证明识别、菜单归属和收起，未验证真实选值或保存。
- 127 项 JavaScript 检查通过（含 17 个本地真实浏览器控件场景），41 项 Python 检查通过。Python/JS 注册声明一致。
- 最后检查 22 页：没有遗留本次展开的菜单。

## 三库结果

|规则库|控件库|注册入口|
|---|---|---|
|`browser/rules/dom.mjs`、`framework_choice.mjs`、`framework_fields.mjs`|`browser/controls/adapters/framework_choice.mjs`、`framework_fields.mjs`|`edge_form_graph/control_catalog.json`|

注册表现为 protocolVersion=1、registryVersion=5，共 23 个可执行声明。整理了 5 种框架选择器（Ant、Moka、Phoenix、ATSX、UD）、Phoenix 自定义单选、Phoenix 日期输入和 UD 日期输入；ATSX 包含有限多选支持。其它框架多选保持延后处理。

修正了：通用规则与框架规则冲突、搜索词误作选中值、日期误判文本、Element Plus 日期外壳吞掉内部输入、嵌套标签丢失、Phoenix 非原生单选漏扫、多选 token 回读不一致、异步多选未等待、异常弹层清理。

## 仍缺操作实现

|类型|数量/现场|状态|
|---|---|---|
|moka-date|7|年/月/日面板导航与提交回读；禁止普通文本 fill|
|moka-hierarchy|6|省市区分层选择、叶子及标签回读|
|ud-range-date|2|开始/结束为同一逻辑范围，两端共同提交与核验|
|ant-picker|2|Ant Design 年月范围，禁止冒用旧 ant-calendar 日期实现|
|moka-select:cascader|58同城/校招面试站点|父级展开与最终叶子选中需分开|
|phoenix-select:region_dialog|中航科创/籍贯|area-selector 地区选择、确定、收起及完整路径回读|

以上缺口已落到 `control-discovery-report.json`，不会注册一个只返回成功的空适配器。58 的级联和 Phoenix 地区弹窗会在普通下拉选项点击前延后处理。


完整页面清单、DOM 证据和私有字段值保存在 `private/open-controls-20260929/`；未加入公共规则或注册表。

运行只读扫描：
```powershell
node scripts/audit_open_controls.mjs private/control-audit
```
扫描不导航、不填表。每页异常单独记录；扫描失败不等于零控件。登记中的17个基本缺口和2个动态变体不会被统计成可执行能力。

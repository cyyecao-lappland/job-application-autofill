# 控件能力契约

实现项目：由 skill 的 `config.local.json` 解析得到的 `project_directory`，见 [本机配置与迁移](installation-config.md)。

实施和扩展标准以项目 `docs/control-contract.md` 为准。新增控件时读该文件，以及 `edge_form_graph/control_catalog.json` 和 `browser/controls/service.mjs`；不要继续往主流程添加适配器名称分支。

- 用户资料使用普通值路径，不新增隐私分类门槛。
- 启动比较两端协议、注册表版本与实际驱动声明，不计算哈希。
- 已结束操作若无法完整回读，记核验跳过并继续；完整回读差异保留为异常。
- 核验跳过不属于核验成功，不能进入方法学习或字段映射固化。
- 调用、控件提交、值比较与网站保存是不同状态；未决调用不能用核验跳过掩盖。
- 本地测试通过不代表真实招聘站点保存已验证。

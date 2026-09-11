# Job Application Autofill · 网申回填 Skill

面向支持浏览器工具和独立子 agent 的 AI 助手：从私有履历 JSON 出发，勘察真实网申表单，独立核验规则，批量填写，再独立核验内容并按模块保存。

这是一套中文工作流和本地辅助工具。浏览器连接由宿主提供；仓库不包含通用网站驱动、账号登录服务或真人履历。

## 能做什么

- 完整盘点履历，结合岗位要求选择呈现内容，记录遗漏与取舍。
- 优先使用 DOM 与语义控件，尊重真实字段、记录边界和保存范围。
- 分开规则核验与内容核验；共享浏览器串行操作。
- 保留已有填写、暂存授权；未知写入先核对，避免重复新增或重复保存。
- 明确区分本地检查通过、页面草稿、网站确认保存和重开验证。
- 最终提交需要独立明确授权；辅助执行器没有自动提交动作。

## 安装与使用

将本仓库作为 `job-application-autofill` skill 文件夹安装到宿主的技能目录。Codex 的常用个人目录是 `~/.codex/skills/`；Windows 对应用户目录下的 `.codex/skills/`。已有同名 skill 时先保留备份，不覆盖正在使用的版本。

```sh
git clone https://github.com/cyyecao-lappland/job-application-autofill.git
```

将克隆得到的文件夹放入上述技能目录，并让宿主重新发现技能。然后：

1. 参考 [示例 JSON](examples/autofill-profile.example.json) 在仓库外准备自己的私有资料，详见 [资料格式](references/profile-format.md)。
2. 打开目标申请页，确保宿主可访问该浏览器，并支持独立子 agent。
3. 明确资料路径、目标页面以及填写/暂存范围。例如：

> 使用 $job-application-autofill，读取我指定的私有履历 JSON，在当前申请页填写可确认的内容并暂存，不提交。缺少的事实集中交接。

示例全部虚构，不能直接用于投递。未提供资料时先指定路径；没有子 agent 能力时只能按默认流程准备资料和勘察，不能假称已完成独立核验。用户明确调整流程时应如实报告省略的核验。

## 依赖与执行方式

- Python 3.11：本地计划、覆盖和快照检查，仅用标准库。
- Node.js 22 或更高版本：可选 adapter 执行器及其测试，仅用内置模块；本地验证环境为 Node.js 24。
- 宿主提供获准的浏览器工具与子 agent。默认 `direct-tool` 使用现有工具；`adapter` 需要自行实现并验证真实浏览器适配器。

入口见 [SKILL.md](SKILL.md)。详细的 [执行与恢复约定](references/execution-contract.md) 和 [独立核验流程](references/independent-checks.md) 说明了能力与限制。

## 本地验证

在仓库根目录执行：

```sh
python -B -m unittest discover -s scripts -p "test_*.py"
node scripts/test_bounded_batch.js
node scripts/test_execute_adapter.js
```

这些测试使用虚构资料和合成浏览器接口，覆盖计划、恢复、未知写入、保存与状态落盘等行为，不证明任意招聘网站均可使用。脚本不能拦截模型绕开流程直接调用浏览器工具，不能独立证明输入证据真实。

## 隐私与贡献

只提交通用方法、代码和虚构测试。履历、证件值、申请截图、运行目录、登录信息与工具原始日志不得提交。忽略规则只是辅助，不是隐私保证；提交前检查实际文件。

报告问题时提供脱敏后的控件结构、复现步骤与合成样例，不附真人申请材料。修改后运行相关测试，说明真实网站验证与离线测试的区别。

## 许可证

[MIT](LICENSE)。

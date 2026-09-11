# Job Application Autofill · 网申 Skill

## 有什么用

让 AI 根据你的履历资料，在招聘网站上填写教育、实习、项目、论文和获奖经历，减少重复录入。

- **按岗位选择内容**：完整盘点经历，结合职位要求和表单容量安排呈现。
- **填写前后分别核验**：先检查页面规则，再对照原始资料检查遗漏、重复和错误。
- **分模块保存、支持续作**：保留已完成进度，交接缺失信息和难操作的字段。

适用于支持本地文件、浏览器操作和独立子 agent 的 AI 助手。需要你提供个人资料并登录招聘网站；默认填写与暂存，最终提交另行授权。

## 怎么用

### 1. 安装

以 Codex 为例，将仓库安装到 [个人技能目录](https://developers.openai.com/codex/skills/)；本地检查需要 Python 3.11。

**Windows · PowerShell**

```powershell
$skillDir = Join-Path $env:USERPROFILE '.agents/skills/job-application-autofill'
New-Item -ItemType Directory -Force (Split-Path $skillDir) | Out-Null
git clone https://github.com/cyyecao-lappland/job-application-autofill.git $skillDir
```

**macOS / Linux**

```sh
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/cyyecao-lappland/job-application-autofill.git "$HOME/.agents/skills/job-application-autofill"
```

已有同名 skill 时先确认版本，避免覆盖。安装后在对话中输入 `$job-application-autofill`；未识别时重启 Codex。浏览器工具和子 agent 能力由宿主提供，需确认可用。

### 2. 准备个人资料

下载 [空白 JSON 模板](templates/autofill-profile.template.json)，另存为 `autofill-profile.json`，放到**仓库外**的私有工作目录：

```text
job-applications/
  private/autofill-profile.json   # 个人履历
  runs/                          # 每次申请的计划、快照和进度
```

模板已列出基本信息、教育、实习、项目、论文、竞赛、奖项、校园经历、技能和语言字段。将 `null` 换成真实值；多段经历复制记录并使用不同 ID，不用的占位记录删除。填写后同步更新 `field_metadata`，核对后设置 `example_only: false`。写法参考 [虚构示例](examples/autofill-profile.example.json) 和 [JSON 资料教程](references/profile-format.md)。

已有简历但没有 JSON，可以先让助手整理：

> 参考此 skill 的资料格式，把我指定的简历整理为 private/autofill-profile.json。保留事实来源，缺失值用 null，列出冲突供我确认。只整理资料，暂不操作招聘页面。

个人 JSON 由你维护，不随仓库提供。证件号码、密码和登录凭据不放入文件；资料被助手读取后可能经宿主发送给模型服务，填写值会进入招聘网站。

### 3. 打开申请页，开始填写

自行登录招聘网站，打开目标职位的申请表。在对话中明确选择浏览器标签页，并将下面的资料路径替换为你的绝对路径：

> 使用 $job-application-autofill。读取「我的 JSON 绝对路径」，填写当前选中的申请页并暂存，不提交。缺失事实集中交接，运行资料保存在私有工作目录的 runs/application-001。

助手会读取资料、核验页面规则、批量填写、核验内容并按模块保存。想先看方法，可将“填写并暂存”改为“先勘察页面并说明填写方案，暂不写入”。

结束时检查三类结果：**确认保存的模块、仍在页面上的草稿、需要你处理的缺项**。页面值匹配不等于保存成功；重开验证需在已确认保存且不会丢失其他草稿时进行。

中断后继续同一任务：

> 继续当前申请，沿用原资料、授权和运行目录。先核对未决操作与当前页面，保留我的手动修改，不重复新增或保存。

## 原理和代码细节

### 工作流程

```text
完整履历 + 职位要求
        ↓
主 agent 勘察页面 → 独立 agent 核验规则
        ↓
生成字段计划 → 本地预检 → 批量填写
        ↓
另一独立 agent 核验内容 → 集中修正 → 分模块保存与回读
```

资料优先级为：本轮用户更正 → JSON 已确认字段 → 有来源的正文。路径按“用户明确指定 → JOB_APPLICATION_PROFILE 环境变量 → 当前目录 private/autofill-profile.json”查找。

浏览器操作优先使用 DOM 和语义控件。默认 **direct-tool** 直接调用宿主工具；**adapter** 模式用于支持本地代码加载与状态落盘的宿主，需要真实适配器和 Node.js。共享浏览器由各 agent 串行操作。

默认一轮预算 30 分钟。写入或保存结果未知时，保留未决状态并先只读核对；恢复时跳过已匹配项，保留用户修改，避免重复新增。规则核验和内容核验使用不同的独立上下文；没有子 agent 时不能声称完成默认核验流程。

### 代码导航

| 文件 | 职责 |
|---|---|
| [SKILL.md](SKILL.md) | 授权、资料、填写、核验和交接规则 |
| [audit_coverage.py](scripts/audit_coverage.py) | 检查经历盘点、选择决策和字段去向 |
| [preflight.py](scripts/preflight.py) | 检查计划、旧值和本批定位证据 |
| [verify_form.py](scripts/verify_form.py) | 对照计划与页面快照，区分匹配和未验证 |
| [connection_guard.py](scripts/connection_guard.py) | 记录连接尝试与未决状态 |
| [bounded_batch.js](scripts/bounded_batch.js) | 管理批次预算、恢复和模块保存 |
| [execute_adapter.js](scripts/execute_adapter.js) | 加载真实适配器，管理文件锁和持久状态 |

协议细节：[独立核验](references/independent-checks.md) · [执行与恢复](references/execution-contract.md) · [计划与快照格式](references/plan-schema.md)。普通用户无需手写计划或适配器。

### 验证与边界

在仓库根目录运行：

```sh
python -B -m unittest discover -s scripts -p "test_*.py"
node scripts/test_bounded_batch.js
node scripts/test_execute_adapter.js
```

发布时通过 48 项 Python 和 35 项 JavaScript 离线测试，使用虚构资料与合成适配器。脚本只用标准库；JavaScript 测试在 Node.js 24 验证，集成测试要求 `python` 命令可用。

预检通过只说明提供的计划与证据满足检查条件，不证明事实真实、网页已保存或所有网站兼容；脚本也不能拦截绕过执行器的直接工具调用。提交前应核对实际申请内容。贡献代码或报告问题时仅附虚构资料，个人 JSON、截图和运行记录留在仓库外。

[MIT License](LICENSE)

# Job Application Autofill · 网申 Skill

## 有什么用

让 AI 根据你的履历资料，在招聘网站上填写教育、实习、项目、论文和获奖经历，减少重复录入。

**核心能力：面向未预先适配网站的 zero-shot 回填。** 无需先为目标网站编写字段映射、选择器或专用填写脚本，agent 在运行时读取真实页面，理解字段含义、记录边界与保存规则，再将你的资料对应到表单。

| | 依赖预置站点模板的填表插件 | 本 skill 的默认流程 |
|---|---|---|
| 字段对应 | 使用事先配置的字段映射与选择器 | 根据当前页面标签、控件和上下文建立映射 |
| 遇到新网站 | 需要新增或调整站点适配 | 从现场勘察开始，无需先提供该站点的适配模板 |
| 遇到页面变化 | 依赖原有规则的兼容性 | 重新观察受影响的字段、依赖关系与保存范围 |

这里的 zero-shot 指**不依赖目标站点专用的预置示例或适配规则**，不是不读取页面、不需要个人资料，或保证任意网站成功。该比较针对固定站点模板方案，不涵盖所有插件；本项目尚未提供跨站点 zero-shot 成功率基准。

- **按岗位选择内容**：完整盘点经历，结合职位要求和表单容量安排呈现。
- **填写前后分别核验**：先检查页面规则，再对照原始资料检查遗漏、重复和错误。
- **分模块保存、支持续作**：保留已完成进度，交接缺失信息和难操作的字段。

本仓库现已包含完整执行程序：edge_form_graph、browser、host、scripts、tests，以及 pyproject.toml、Node 依赖清单和启动入口。程序复用正式映射，集中处理陌生字段并按网站真实保存范围核验。个人资料、正式映射和投递记录仍通过私人渠道迁移。填写、保存和正式提交沿用当前用户明确授权的范围；下载 skill 本身不代表授权。

仓库还包含独立的 [公司抽取与招聘筛选 skill](skills/recruitment-batch-screening/SKILL.md)：从已有候选 JSON 抽取公司／岗位、按真实台账去重，并编排 Luna 实时浏览官网审核。它与网申回填共用仓库，保留各自的入口和执行代码。

## 怎么用

### 新电脑缺少网申专用 Edge

点击本仓库 **Code → Download ZIP**，解压后双击根目录 **install-edge.cmd**。脚本自动检测 Edge（缺少时通过 winget 安装）、创建独立浏览器用户目录和桌面的 **网申专用 Edge** 入口，并检查 `http://127.0.0.1:9333` 连接。首次使用需在新电脑登录招聘网站。

已有网申执行环境的用户只需这一步；新用户继续按下文安装执行程序。详情及自定义目录见 [Windows Edge 部署说明](docs/windows-edge-setup.md)。

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

### 同时安装公司抽取与招聘筛选 skill

`skills/recruitment-batch-screening` 保留完整独立 skill。若要在任意工作区使用它，请将该目录安装到个人技能目录。在本仓库根目录执行以下 Windows PowerShell 命令：

```powershell
$skillsDir = Join-Path $env:USERPROFILE '.agents/skills'
New-Item -ItemType Directory -Force $skillsDir | Out-Null
Copy-Item -LiteralPath .\skills\recruitment-batch-screening -Destination $skillsDir -Recurse -Force
```

macOS/Linux 可复制同一目录至 `~/.agents/skills/recruitment-batch-screening`。安装后使用 `$recruitment-batch-screening`；避免同时保留多个同名安装。它的 Python 程序只用标准库，无需另外安装 Python 包；Luna 审核仍需要宿主提供实际子代理和浏览器工具。

后续先在本仓库执行 `git pull`，再重复复制命令更新筛选 skill。个人运行配置保存为 `config.local.json`；可复用的个人台账绑定保存为 `references/local-sources.local.json`，这两个文件均不随 Git 提交，复制更新时也不会被公开模板覆盖。文件格式、运行命令与来源要求见 [筛选 skill](skills/recruitment-batch-screening/SKILL.md)。

公开版本不附带公司台账、候选池、个人履历或审核记录。所有输入路径应改为新电脑的绝对路径，`confirmed_via` 必须写明实际核验来源；默认排除源为空，未配置真实来源会停止。只有部分恢复快照时，完整历史去重仍有缺口。该 skill 是当前保留的候选抽取与审核编排程序，旧 LangGraph 抽取器源码未包含在内。

### 2. 安装执行程序并配置本机位置

仓库根目录就是执行项目，不需要另找 job-application-langgraph 源码。Windows 使用 Python 3.11 或以上及 Node.js，从克隆后的目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm ci
```

macOS/Linux 使用 `.venv/bin/python` 安装 Python 项目，并运行 `npm ci`。模型调用还需要本机可用且已登录的 Codex CLI；环境安装检查不调用真实模型或招聘网站。

若尚无本机配置，复制 `config.example.json` 为 `config.local.json`。新示例中的 `workspace_root` 和 `project_directory` 都是 `.`，指向当前仓库；`profile_file` 改为你真实个人资料的路径，也支持绝对路径。其他系统还需设置实际 Python 路径，如 `.venv/bin/python`。

```powershell
Copy-Item .\config.example.json .\config.local.json
.\.venv\Scripts\python.exe .\scripts\resolve_config.py
```

已有 `config.local.json` 时保留，不用示例覆盖。旧配置仍可指向 scp 迁移的完整执行项目；若改用此仓库的执行代码，将 `project_directory` 改为仓库绝对路径，核对 `python_executable`，并保留原资料与任务根目录。

正式映射、控件方法和程序私有配置属于个人运行数据。已有迁移资料时，从原执行项目的 `private` 中保留这些文件及原申请记录，或配置正确的绝对资料/映射路径；勿用空模板覆盖原映射。程序的 `private/local-config.json` 示例在 `templates/local-config.example.json`。仅全新资料、没有历史映射时，可用 `templates/form-knowledge.empty.json` 初始化 `private/form-knowledge.json`。

路径检查只验证必要文件存在，不证明依赖、模型、浏览器或网站保存已经可用。程序依赖安装可通过下面的离线命令检查：

```powershell
.\.venv\Scripts\python.exe -m edge_form_graph.application_cli --help
node scripts/build_control_registry.mjs --check
```

后续在仓库运行 `git pull` 即可同时更新 skill 和执行代码，个人配置与记录保持独立。完整执行接口见 [执行程序说明](docs/executor-guide.md)，配置与迁移边界见 [本机配置与迁移](references/installation-config.md)。

### 3. 准备个人资料

下载 [空白 JSON 模板](templates/autofill-profile.template.json)，另存为 `autofill-profile.json`，放到**仓库外**的私有工作目录：

```text
job-application/                 # config.local.json 的 workspace_root
  evidence-private/recruitment-autofill/autofill-profile.json
  open-source/job-application-langgraph/
    private/                    # 正式映射、任务记录与投递证据
```

模板已列出基本信息、教育、实习、项目、论文、竞赛、奖项、校园经历、技能和语言字段。将 `null` 换成真实值；多段经历复制记录并使用不同 ID，不用的占位记录删除。填写后同步更新 `field_metadata`，核对后设置 `example_only: false`。写法参考 [虚构示例](examples/autofill-profile.example.json) 和 [JSON 资料教程](references/profile-format.md)。

已有简历但没有 JSON，可以先让助手整理：

> 参考此 skill 的资料格式，把我指定的简历整理为 private/autofill-profile.json。保留事实来源，缺失值用 null，列出冲突供我确认。只整理资料，暂不操作招聘页面。

个人 JSON 由你维护，不随仓库提供；正式映射、投递台账和原 journal 也不上传。仅在用户当前授权的申请范围内使用资料，缺失事实不猜测。

### 4. 打开申请页，开始填写

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
读取页面与资料 → 程序复用已核验映射
        ↓
集中处理陌生字段 → 同一执行器批量填写
        ↓
按真实保存范围核验 → 保存确认与回读 → 激活已验证经验
```

资料优先级为：本轮用户更正 → JSON 已确认字段 → 有来源的正文。安装位置由 `config.local.json` 统一解析；资料路径显式交给执行器。配置缺失或无效时报告，不回退旧电脑路径或示例资料。

zero-shot 来自运行时的三步推断：从完整履历中选择有来源的事实，依据现场页面建立字段与记录映射，再由独立 agent 核验规则和内容。个人 JSON 是事实库，不是某个网站的字段模板；通用浏览器工具负责交互，站点经验只作为可选参考，不能替代现场验证。

浏览器操作优先使用 DOM 和语义控件。正常入口是执行项目的 `scripts/run_edge_cdp_application.mjs`，显式接收配置解析的程序、资料、Python 与本机 Edge CDP 地址，复用同一已登录页面。旧 direct-tool 与 adapter 资料保留用于历史状态恢复。配置不会自动启动浏览器、复制登录态或批准网页写入。

预算沿用原任务开始时间。写入或保存结果未知时，保留未决状态并先只读核对；恢复时跳过已匹配项，保留用户修改，避免重复新增。已由正式映射证明并执行回读一致的内容使用程序核验，陌生语义及需要独立内容审查的范围使用独立模型核验；两类证据分别报告。

### 代码导航

| 文件 | 职责 |
|---|---|
| [SKILL.md](SKILL.md) | 授权、资料、填写、核验和交接规则 |
| [config.example.json](config.example.json) | 可提交的安装配置示例 |
| [pyproject.toml](pyproject.toml) | Python 执行程序及依赖 |
| [tests/](tests/) | 执行程序的离线回归测试 |
| [run_edge_cdp_application.mjs](scripts/run_edge_cdp_application.mjs) | 已登录 Edge 页面上的原生执行入口 |
| [resolve_config.py](scripts/resolve_config.py) | 只读解析安装路径并检查必要文件 |
| [audit_coverage.py](scripts/audit_coverage.py) | 检查经历盘点、选择决策和字段去向 |
| [preflight.py](scripts/preflight.py) | 检查计划、旧值和本批定位证据 |
| [verify_form.py](scripts/verify_form.py) | 对照计划与页面快照，区分匹配和未验证 |
| [connection_guard.py](scripts/connection_guard.py) | 记录连接尝试与未决状态 |
| [bounded_batch.js](scripts/bounded_batch.js) | 管理批次预算、恢复和模块保存 |
| [execute_adapter.js](scripts/execute_adapter.js) | 加载真实适配器，管理文件锁和持久状态 |

协议细节：[独立核验](references/independent-checks.md) · [执行与恢复](references/execution-contract.md) · [计划与快照格式](references/plan-schema.md)。普通用户无需手写计划或适配器。

### 验证与边界

在仓库根目录运行：

```powershell
$env:JOB_APPLICATION_PYTHON = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
$env:PATH = (Join-Path (Get-Location).Path '.venv\Scripts') + ';' + $env:PATH
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s scripts -p "test_*.py"
.\.venv\Scripts\python.exe -B -m unittest discover -s skills/recruitment-batch-screening/scripts -p "test_*.py"
node --test tests/test_*.mjs
node scripts/test_bounded_batch.js
node scripts/test_execute_adapter.js
node scripts/test_direct_batch.js
node scripts/test_direct_playwright.js
```

本次更新通过执行程序 466 项 Python 测试、skill 辅助脚本 71 项 Python 测试、执行程序 Node 测试及 4 组原有 JavaScript 检查。全新 Python 环境完成 `pip install -e .`，Node 依赖完成 `npm ci`。可选 E5 服务另通过 31 项测试、跳过 2 项依赖模型的测试；这不证明真实模型推理已验证。以上均为离线检查，使用虚构资料与合成浏览器／适配器，不连接真实招聘网站或提交申请。JavaScript 测试在 Node.js 24 验证，集成测试要求 `python` 命令可用；macOS/Linux 请改用 `.venv/bin/python` 与对应环境变量语法。

公司抽取与筛选 skill 随仓库发布时另通过 19 项离线测试，包括未配置排除源时停止、本机绑定优先级及审核证据检查；这不代表已完成新的官网审核。

预检通过只说明提供的计划与证据满足检查条件，不证明事实真实、网页已保存或所有网站兼容；脚本也不能拦截绕过执行器的直接工具调用。提交前应核对实际申请内容。贡献代码或报告问题时仅附虚构资料，个人 JSON、截图和运行记录留在仓库外。

[MIT License](LICENSE)

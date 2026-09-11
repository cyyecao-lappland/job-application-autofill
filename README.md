# Job Application Autofill · 网申回填 Skill

从你自己的私有履历 JSON 出发，在真实招聘网站上勘察表单、独立核验规则、批量填写、独立核验内容，并按模块暂存。

**开始前需要：个人履历 JSON、可访问当前申请页的浏览器工具，以及支持独立子 agent 的宿主。** 本仓库提供中文工作流和本地检查工具，不附带真人履历、招聘账号、通用浏览器驱动或自动登录服务。它不会仅凭运行一条 Python 命令就填完整个网站。

## 教程导航

1. [确认运行条件](#1-确认运行条件)
2. [安装-skill](#2-安装-skill)
3. [准备你的私有-json](#3-准备你的私有-json)
4. [指定资料路径](#4-指定资料路径)
5. [打开页面并开始填写](#5-打开页面并开始填写)
6. [检查结果与继续任务](#6-检查结果与继续任务)
7. [常见问题](#7-常见问题)
8. [开发者工具与测试](#8-开发者工具与测试)

## 1. 确认运行条件

| 条件 | 用途 | 是否必需 |
|---|---|---|
| 能读取 skill、访问本地文件和执行本地命令的 AI 宿主 | 读取资料、生成计划与运行检查 | 是 |
| 宿主支持的浏览器工具 | 观察和操作用户指定的真实申请页 | 是；本仓库不自带 |
| 独立子 agent | 一名核验页面规则，另一名核验内容 | 默认流程必需 |
| Python 3.11 | 计划、完整性和页面快照检查 | 是；脚本只用标准库 |
| Node.js | adapter 执行器与 JavaScript 测试 | direct-tool 填写不需要；adapter 开发建议 22+，已在 24 验证 |
| Git | 克隆、更新本仓库 | 使用下方命令安装时需要 |

先在宿主中确认它确实能读取目标浏览器页。普通聊天窗口、仅能访问远程网页的搜索工具，或没有浏览器连接的命令行环境，不具备完整填写能力。浏览器工具的权限和支持范围以该工具当前文档为准。

默认 `direct-tool` 由 agent 使用现成浏览器工具，无需编写 `batch.js`。只有宿主支持加载本地代码、真实浏览器适配器和可靠状态落盘时，才使用 `adapter` 模式。

## 2. 安装 skill

当前 [Codex 官方技能文档](https://developers.openai.com/codex/skills/) 使用 `~/.agents/skills/` 作为个人技能目录，也支持项目内的 `.agents/skills/`。以下以个人安装为例。其他宿主按其自己的 skill 加载规范安装；历史版本或现有安装可能使用其他目录，不要盲目复制出多个同名 skill。

### Windows · PowerShell

```powershell
$skillDir = Join-Path $env:USERPROFILE '.agents/skills/job-application-autofill'
New-Item -ItemType Directory -Force (Split-Path $skillDir) | Out-Null
git clone https://github.com/cyyecao-lappland/job-application-autofill.git $skillDir
```

### macOS / Linux

```sh
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/cyyecao-lappland/job-application-autofill.git "$HOME/.agents/skills/job-application-autofill"
```

如果目标文件夹已存在，先确认是否是你正在使用的版本；不要删除或覆盖它来绕过提示。也可下载仓库 ZIP，解压后将包含 `SKILL.md` 的文件夹命名为 `job-application-autofill`，放入技能目录。

Codex 会自动发现技能变化；如果未出现，重启 Codex。在对话中输入 `$job-application-autofill`，让助手先确认能读取该 skill。使用项目级安装时，确认当前任务位于对应项目范围内。

## 3. 准备你的私有 JSON

**JSON 是你自己维护的事实资料，不是仓库提供的个人数据，也不是网站导出的页面快照。** agent 先完整读取它，再把有来源的事实对应到当前网站字段。网站没有对应栏目、资料不全或控件无法可靠操作时，应明确交接。

详细字段说明见 [私有资料 JSON 教程](references/profile-format.md)，格式起点见 [虚构示例](examples/autofill-profile.example.json)。`career-library` 之类的履历库可选；完整 JSON 已有可靠正文时不需要另建一个库。

### 新建一个仓库外的申请工作目录

推荐结构：

```text
job-applications/                 你的私有工作目录，不是公开 skill 仓库
  private/
    autofill-profile.json        你维护的事实资料
    sources/                     可选：原始履历与证明资料
  runs/
    application-001/              本次运行的计划、快照、状态与交接
```

Windows 示例（继续使用上一步的 `$skillDir`）：

```powershell
$applicationRoot = Join-Path $env:USERPROFILE 'job-applications'
New-Item -ItemType Directory -Force (Join-Path $applicationRoot 'private') | Out-Null
$profilePath = Join-Path $applicationRoot 'private/autofill-profile.json'
if (-not (Test-Path -LiteralPath $profilePath)) {
    Copy-Item -LiteralPath (Join-Path $skillDir 'examples/autofill-profile.example.json') -Destination $profilePath
}
```

macOS / Linux：

```sh
mkdir -p "$HOME/job-applications/private"
if [ ! -e "$HOME/job-applications/private/autofill-profile.json" ]; then
  cp "$HOME/.agents/skills/job-application-autofill/examples/autofill-profile.example.json" "$HOME/job-applications/private/autofill-profile.json"
fi
```

用文本编辑器打开该文件：

1. 替换或删除所有虚构经历，再填入你自己的已确认资料。
2. 每条经历保留稳定 `record_id`、来源 `source`、真实日期和正文。
3. 不知道的值用 `null` 并说明状态；空数组不自动表示“本人没有”。
4. 保留论文真实状态、作者身份和指标归属；年月不能擅自补成某日。
5. 完成个人核对后设 `example_only: false`，删除示例告示。此标记是给 agent 阅读的声明，不是程序级身份或真实性校验。

不要把身份证号、护照号、密码、Cookie、令牌或验证码放入此 JSON；证件号由用户自己在网站输入。常规联系方式按你愿意提供的范围维护。

### 已有简历，但不会写 JSON

先把原始材料留在私有工作目录中，给助手一个仅整理资料的请求：

> 请根据我指定的本地简历和补充材料，参考此 skill 的资料格式，整理为 private/autofill-profile.json。只整理资料，不操作招聘页面。逐项保留事实来源；缺失值用 null，不编造日期、指标、作者顺序或投稿状态。列出冲突和待确认项，供我核对。

核对生成的 JSON 后再用于填写。不同版本的履历发生冲突时，以你当前明确更正的内容优先；不要把模型生成的推断当作已确认事实。

### 检查 JSON 是否能解析

Windows：

```powershell
python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8-sig')); print('JSON syntax OK')" $profilePath
```

macOS / Linux：

```sh
python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8-sig')); print('JSON syntax OK')" "$HOME/job-applications/private/autofill-profile.json"
```

该检查不打印个人内容，只检查语法，不验证资料真实性或网站兼容性。JSON 不允许注释和末尾多余逗号。

## 4. 指定资料路径

skill 的查找顺序是：

1. 当前请求里明确提供的 JSON 路径。
2. 宿主进程能读取到的 `JOB_APPLICATION_PROFILE` 环境变量。
3. 当前任务工作目录下的 `private/autofill-profile.json`。

**推荐直接提供绝对路径**，最容易确认是哪份资料。使用默认位置时，应将任务工作目录设为上面的 `job-applications`，而不是 skill 仓库。

可选环境变量示例：

```powershell
$env:JOB_APPLICATION_PROFILE = $profilePath
```

```sh
export JOB_APPLICATION_PROFILE="$HOME/job-applications/private/autofill-profile.json"
```

这些设置只影响当前 shell 及其后续子进程，已运行的桌面应用通常不会因此收到变量。环境变量由 agent 按 skill 约定读取，不是辅助 Python 脚本自动加载的配置。桌面应用无法读取时，直接在对话中提供路径即可。

## 5. 打开页面并开始填写

1. 自己登录招聘网站，打开目标职位的申请表。
2. 在宿主中选择或明确指出正确的浏览器、标签页和职位；不要仅说“帮我填一下”。
3. 明确资料路径及“填写”“暂存”“提交”的范围。首次可以只让助手勘察规则和说明方法。

首次勘察请求：

> 使用 $job-application-autofill。资料在我指定的 JSON 绝对路径，目标是当前选中的招聘申请页。先读取资料并勘察页面，说明可填写范围、缺少的事实和保存方式，暂不写入页面。

确定范围后的填写请求：

> 按刚才确认的范围填写并暂存，不提交。使用同一份私有 JSON 和当前申请页，先完成独立规则核验，批量填写后由另一名 agent 核验内容，再按网站实际模块保存。未知事实集中交接，不补造信息；运行资料存放在私有工作目录的 runs/application-001。

也可在首次请求中直接授权相同范围的填写和暂存；skill 会沿用已有授权，不要求每批次重复确认。JSON 中的呈现偏好本身不是网站操作授权。

实际流程：

| 阶段 | 助手应完成的工作 |
|---|---|
| 资料盘点 | 覆盖所有相关经历，保留来源；按真实 JD 和网站容量安排去向 |
| 页面勘察 | 确认重复记录、下拉选项、必填条件、动态依赖和保存范围 |
| 独立规则核验 | 独立上下文先观察页面，再核对主 agent 的规则清单 |
| 批量填写 | 使用可靠控件方法，短批次执行；遇到未知写入先核对 |
| 独立内容核验 | 另一独立上下文对照原始资料和页面，检查遗漏、重复和错误 |
| 修正与暂存 | 修正受影响项，满足真实保存条件后按模块保存并读取结果 |
| 交接 | 分清保存内容、页面草稿和待处理项，保留页面供用户接手 |

两名核验者共享浏览器时串行交接。没有独立子 agent 时，默认只能继续准备与勘察；如果你明确调整流程，助手应说明没有执行独立核验，不能自称已通过。

## 6. 检查结果与继续任务

结束报告应至少说明：已经填写哪些栏目、哪些模块确认保存、哪些仍是草稿、哪些项目未处理，以及原因。

| 报告状态 | 实际含义 |
|---|---|
| `planReady` / `ready` | 本地计划或记录的执行条件通过；不代表已经填写或保存 |
| 页面匹配 / 页面草稿 | 当前输入框里读到了预期内容；不证明网站已保存 |
| 网站确认保存 | 实际保存动作后，观察到成功提示或保存后的只读记录等证据 |
| 重开验证 | 保存确认后，在不丢失其他草稿的前提下重开并回读，内容仍保留 |
| 已投递 / 已提交 | 必须有单独提交授权和真实提交结果；本 skill 的辅助执行器不自动提交 |

遇到中断或超时，可以这样继续：

> 继续当前申请，沿用原来的资料、授权和 runs/application-001。先核对未决操作与当前页面；不要重复新增记录或重新点击未知状态的保存。保留我手工修改的内容，冲突项交接。

不要通过刷新页面、清空状态目录或新建运行目录来猜测是否成功。若上一请求仍未结束，先等待或确认其状态。默认一轮总预算为 30 分钟，包含准备、填写与核验；耗尽后先交接，需要继续时明确续作范围与新预算。

## 7. 常见问题

**可以不提供 JSON 吗？** 需要可靠事实资料才能回填。没有 JSON 时，先按第 3 节整理并核对；不能用公开示例代替个人资料。已有不同结构的 JSON 可继续使用，只要分类和字段语义清楚，详见资料教程。

**是不是安装 skill 就能控制任何浏览器？** 不是。宿主必须已经提供适用的浏览器工具和授权。不要因为本地检查通过就声称浏览器连接可用。

**为什么某个日期或下拉没填？** 可能资料精度不足、网站没有真实匹配选项或方法尚未验证。助手应说明具体原因，继续独立的确定项；不能补造日期、赛事类别或证书来填满。

**为什么没有保存？** 网站可能没有保存按钮，或某模块仍缺必填项。此时只能报告页面草稿或该模块受阻，不能把其他已经保存的栏目也混称为草稿。

**需要自己写 alignment-plan.json 或 batch.js 吗？** 普通用户无需手工写。主 agent 根据资料和现场页面生成计划及证据；默认 direct-tool 不需要 batch.js。开发适配器时再读执行约定。

**运行数据是否都留在本地？** 文件可以存放在本地，但 agent 阅读的内容可能经宿主发送给其模型服务，填写值会进入目标招聘网站；数据处理以宿主、工具和网站的实际配置与政策为准。仓库里的辅助脚本没有内置上传或遥测客户端，不等于整个 AI 工作流离线。

**能保证没有错误吗？** 不能。独立核验和辅助检查减少遗漏与重复操作，但不会独立证明事实来源真实，也不能阻止模型绕过脚本调用工具。提交前仍应核对实际申请内容。

## 8. 开发者工具与测试

在 skill 仓库根目录运行现有离线测试：

```sh
python -B -m unittest discover -s scripts -p "test_*.py"
node scripts/test_bounded_batch.js
node scripts/test_execute_adapter.js
```

macOS / Linux 如没有 `python` 命令，可使用可用的 Python 3.11 环境；adapter 集成测试会调用 `python`，需确保它指向该环境。

发布时验证了 48 项 Python 和 35 项 JavaScript 测试，使用虚构资料与合成适配器，不能代表所有真实网站兼容。

| 工具 | 用途 |
|---|---|
| `scripts/audit_coverage.py` | 检查经历盘点、选择决策和操作去向是否对应 |
| `scripts/preflight.py` | 检查计划、旧值、执行方式和本批定位证据 |
| `scripts/verify_form.py` | 比较提供的页面快照；不连接网站、不执行保存 |
| `scripts/connection_guard.py` | 记录连接尝试与未决状态；不创建浏览器连接 |
| `scripts/bounded_batch.js` | 在真实适配器支持下执行有预算、可恢复的批次 |
| `scripts/execute_adapter.js` | 加载真实 adapter 工厂，管理锁和持久状态 |

读取 [SKILL.md](SKILL.md)、[资料格式](references/profile-format.md)、[独立核验](references/independent-checks.md)、[执行与恢复](references/execution-contract.md)、[计划与快照格式](references/plan-schema.md) 了解协议。离线检查的通过结果不能代替独立核验，也不构成新授权。

## 隐私与贡献

只提交代码、通用方法和虚构示例。个人 JSON、证明材料、申请截图、运行目录与登录信息都应放在仓库外；`.gitignore` 只能辅助避免误提交，不是权限隔离，也不能清除已经进入 Git 历史的文件。

报告问题时只附脱敏控件结构、复现步骤与合成资料。不要上传真实履历来帮助维护者复现。修改后运行相关检查，区分离线测试与真实网站证据。

## 许可证

[MIT](LICENSE)。

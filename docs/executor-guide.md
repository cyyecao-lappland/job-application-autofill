# Edge Form Graph v0.1

执行程序现已包含在仓库根目录。安装与资料位置以根目录 README 和 config.local.json 为准；现有迁移资料与原任务位置仍可通过显式路径使用。新运行目录必须位于 OneDrive 外。

检查点由 `edge_form_graph.storage.SqliteSaver` 保存：共享压缩块使用普通数据库记录 ID 和字节相等比较，不使用哈希或校验和。旧格式仍可读取；新库必须通过该类读取，不能用上游默认 serializer 解码，也不要为诊断复制整份运行库。

每次协调器退出、图任务停止后自动清理历史：每个主流程保留最近 32 个检查点，另保留最近的未知保存核对状态、所需的子流程及跨流程引用。保留的检查点继续使用原 ID 和中间写入；边界以前的历史步骤与无引用压缩块被回收，空闲页达到阈值时自动缩小文件。原保存命令关联与模块定位另存紧凑索引，旧格式首次清理前先补全索引。当前资料、当前结果、writer journals、投递证据和未决操作保护继续保留；没有按年龄或“失败”状态删除整个任务。这控制了重复历史的增长，新增申请与真实证据仍会增加少量空间，不能承诺整个运行环境小于 1 MB。

独立本地字段向量检索服务见 [Local E5 Service](../inference-service/README.md)：ONNX INT8 CPU、单 FIFO 队列、SQLite 字段语义库与确认反馈采集。

读取 → 程序填写已知项 → agent 集中处理异常并由程序补填 → 核验与保存 → 固化已验证规则。

当前 `application` 入口由固定程序流程负责模块顺序、JSON 取值、映射表查询、控件执行、回读和保存。Agent 只参与开关允许的异常匹配与核验，不决定下一步浏览器动作。旧单模块入口仅为兼容路径。

### 程序扫描整页与覆盖检查

原生 Playwright 入口现在可以省略 `--manifest`。程序通过 `browser/page_inventory.mjs` 读取当前页面，识别表单卡片、独立保存按钮、重复记录和新增按钮；`edge_form_graph/page_planner.py` 按页面顺序生成清单。显式传入清单仍作为兼容方式，但不能据此声称已经扫描整页。

```powershell
node scripts/run_edge_cdp_application.mjs --start --page-id <已有页面ID> --run-dir <运行目录> --prior-root <历史运行目录> --agent-tuning off --control-agent on --allow-save
```

保存范围和资料模块分离：同一个卡片可以包含学历、论文、语言等不同来源。重复记录通过实际名称和起始年月匹配 `record_id`，先复用空行，再新增缺少的记录。相同学校不同就读时期不会合并；用户排除的经历和未要求的高中不新增；在审稿件不当作已发表论文。现有记录身份不明确时保留并报告，不猜测覆盖。

当前已支持 Fusion 的表单卡片及行内重复记录结构，同时盘点普通 form。未知结构、无法分类的保存/声明边界、未核验的折叠卡片、iframe 和缺少来源的附件都保留为覆盖缺口。**这不是任意网站已全部适配的承诺。** `page-inventory.json` 是现场读取，`generated-manifest.json` 是程序计划；只有实际执行和保存回读才算完成。`incomplete_coverage` 不代表成功。

已结束但保存结果未知的旧命令仍保留原 journal。只有在同一标签页重新证明保存区域互不包含、旧调用已结束后，才能继续其他独立区域；原区域保持 `prior_save_unknown`，不得重放保存。未结束调用、范围重叠或定位不唯一仍停止。独立控件失败纳入缺口，同范围有缺口时不能自动保存为完整结果。

Fusion 的“开始月份 + 至今”按完整复合控件处理：资料必须明确 `is_current=true`，填写开始月份、保留空结束月份、选中并回读“至今”、关闭面板，再核对日期与勾选状态。不会补造结束日期。保存后有些卡片只把内部记录切换为 `next-form-preview`；程序按记录逐字段回读，并核对日期展示中的“至今”，不能仅凭外层 form 还存在就判定保存失败，也不能用另一条记录的文字冒充匹配。

### Agent 调优开关

搜索选择控件使用独立 `search_select` 操作模式，识别函数在 `browser/rules/`，异步候选等待在 `browser/controls/search_select.mjs`（原路径保留兼容导出）。静态条件为可编辑输入框加 combobox/已知选择器结构；已知日期面板、dialog/grid/tree 弹层不按搜索下拉处理。展开后依据实际输入位置和归属明确的候选列表确认，不使用业务字段名称分类，也不凭可输入就认定支持自由文本。

执行时先用已解析的目标值搜索，再有界等待候选；菜单尚未挂载、正在加载或重新渲染时重新读取定位。只有唯一、非禁用的精确选项才能点击，之后继续走原执行器的已选值回读。重复候选、菜单归属改变、只有搜索文字而未选中均不算成功。字段未取得搜索词且当前无候选时返回 `search_query_required`，不会把首项兜底的内部标记当作查询词。该模式不凭 DOM 推断是否联网，不自动允许创建自由文本选项。

电话区号是精确匹配的受限补充：目标必须是完整的 `+数字`，候选文本必须仅含一个完全相同的区号，且候选唯一；`+86` 不匹配 `+860`，同一区号的多个国家选项不自动选第一项。普通城市、学校等名称仍要求精确匹配。

层级候选可以用末级名称搜索，但最终仍匹配完整路径。例如搜索“衡阳”后，只接受预期的“中国-湖南-衡阳”。阿里城市路径由明确的家庭/校区地址程序转换，不由控件执行器推断地址。Fusion 的 `next-select-tag` 用真实标签内容回读；单值替换会移除旧标签、选择精确候选，再通过 Escape/Tab 收起菜单。当前只支持这一单值目标路径，不声称支持任意多值集合或无候选时创建自由文本。

Fusion 保存后可能保留 `form`，同时切换到 `next-form-preview`。保存核验要求预览类、没有编辑控件且全部已核验普通值可见；不会因表单节点仍存在就重复保存。保存回执已结束但当时未确认时，后来的保存回读必须由历史主图命令关联到原保存命令，才能解除旧保存门槛，不能用任意后续成功清除未结束调用。

字段取值与控件操作分开：`网页模块 + 字段名 → 通用字段映射 → JSON 来源 → value`；随后由 `控件类型 → 执行器` 完成输入。`edge_form_graph/field_mapping.py` 独立负责资料模块归一化、字段同义词和来源索引，不读取 DOM、控件类型或个人答案。

个人模块的家庭所在城市从明确的家庭地址提取；学校所在城市仅从唯一 `is_current: true` 的教育记录读取校区地址。地址必须显式包含直辖市或省、市前缀，缺失或当前教育记录不唯一时不猜测。这些是代码内的来源规则，不能称为已通过网站保存验证的学习映射。

现有 `field_map` 文件无需覆盖迁移：加载时将已验证、无条件、无控件依赖且来源属于同一资料模块的事实关系，投影为 `(资料模块, 同义字段名)` 字典，跨网站和文本/下拉复用。例如 `personalInfo/姓名` 与 `个人信息/您的姓名` 都能找到 `/identity/name`；常见姓名、邮箱、手机号另有标准资料结构映射。重复经历仍必须绑定 `record_id`，重排后重新解析资料位置。公司专属、条件/依赖规则、未知资料结构仍保留站点范围；冲突关系不会任选一条。控件写入能力和选项等价表仍由后续执行层单独验证。

在原生 Playwright 启动命令或 `application start` / `fresh-start` 后加 `--agent-tuning off`，关闭本次任务的模型调用；`--agent-tuning on` 开启异常调优。也可在本机 `private/local-config.json` 增加布尔字段 `"agent_tuning": false`。优先级为启动参数 > 本机配置 > 默认开启。

```powershell
node scripts/run_edge_cdp_application.mjs --start --endpoint http://127.0.0.1:9333 --page-id <已有页面ID> --run-dir <运行目录> --manifest <模块清单路径> --prior-root <既有记录目录> --agent-tuning off
```

需要保存时沿用 `--allow-save`。开关写入任务状态，`resume` 沿用原值；更换模式需启动新的读取任务，不能在尚未结束的写入中切换。

关闭时仍优先查询映射库、解析已绑定记录的字段别名、读取 JSON，并跳过已经匹配的值。无法取得可执行答案时由独立策略模块 `edge_form_graph/execution_policy.py` 生成兜底动作：

- 文本填写 **`识别失败！需人工填写！`**。
- 原生下拉及已有可用执行器的自定义下拉，按当前可读选项顺序选择第一个可用项，跳过禁用项和“请选择”。不把内部占位标记输入搜索框。
- 字段映射、陌生枚举判断和内容核验均不调用模型；程序仍检查实际回读、必填项及保存结果。动态新增字段会在有限次数内重新进入程序映射。
- 兜底字段在 `agent-report.json` / `agent-report.md` 的 `manual_review` 中列出；结束状态为 `complete_with_fallbacks`，不算事实核验通过。兜底计数与真实资料填写计数分开，含兜底的保存范围不写入映射学习库。

此开关不能凭空提供未知控件的操作方式。没有可用候选、控件尚不支持、日期/文件/验证码、受保护字段及未结束的写入仍保留明确问题状态；不靠模型接管继续。当前独立运行入口仍需要已发现的模块与保存范围清单，尚未实现任意新网站从入口到结束的全自动发现。本次验证为离线回归测试，未将兜底值写入真实简历，也没有新的真实网站速度结论。

### 拼多多校园招聘现场经验

- `careers.pddglobalhr.com/campus/resume-apply` 的经历卡片使用 Rocket Select、Ant Select 和月份面板混合实现；学校选择需要先打开搜索框、输入完整校名、等待远程候选，再点击唯一精确项。
- 硕士教育会动态出现“是否保研”，本科教育会动态出现“是否专转本/专升本/自考本科/持有前置专科学历”。这些字段必须在学历选择后重新扫描，不能沿用选择学历前的控件清单。
- 卡片保存接口现场可能约 8 秒后才从“取消/保存”变为“删除/编辑”。保存核验应检查当前可见按钮文字，不应只检查复用的 CSS 类，也不能在短暂等待后重复点击。
- 语言卡片刷新后可能暂时显示内部枚举代码。进入编辑态读取六个选择框的中文值，再取消编辑，可以区分真实丢值和只读展示未加载枚举的问题。
- 职位方向、是否服从调剂、第二志愿和招聘渠道属于投递页公共临时字段，刷新会清空。应先刷新核验已保存卡片，最后一次性填写公共字段并停在“简历投递”按钮前。

## 程序优先与持续学习

- `private/local-config.json` 的 `semantic_model` 固定为用户指定的 `gpt-6-luna`；`knowledge_file` 默认 `private/form-knowledge.json`。`review_model` 同样固定为 `gpt-6-luna`，用于独立内容核验；不修改用户全局 Codex 设置；模型不可用时报告错误，不静默切换。
- 原 JSON 继续作为信息表。知识文件的 `field_map` 保存网站字段到 JSON 来源的关系，`enum_map` 保存下拉等价名称；不存在时视为空库，首个验证成功关系才创建，损坏时明确报错。
- 知识键包含站点/路径、模块语义和字段语义。重复记录在模块清单中可附 `mapping_context: {module_type, record_collection, record_id}`；未绑定时由批量语义匹配给出绑定并验证唯一性。缓存只保存记录内相对路径，运行时按 `record_id` 重新找位置。数组下标变化不能串用资料。
- 已知字段先派发，陌生字段同模块集中一次调用 `match_unknown`；模型输出来源指针、转换、依赖、判断依据，不能输出定位脚本或直接操作浏览器。新叫法与条件推理分开记录；例如 `enrolled_no_diploma` 只在明确在读为真时返回否，条件不成立则重新判断，不能倒推已获得证书。
- 独立核验通过后仍只保留候选；保存确认且保存后回读一致，再通过 `compile_saved` 原子激活整个保存范围的字段关系和枚举关系。未经核验、未决调用、冲突不会被当成成功学习。规则碰撞进入 conflicted，不用最后一次结果覆盖旧规则。
- 保存前采用分级核验：当保存范围内每个非保密字段都由正式映射表或受限的记录内字段规则命中、本轮没有语义模型参与、执行结果为 `written/already_matched` 且程序回读一致时，由程序直接生成完整核验结果；出现无法由受限规则解释的新字段、新枚举、未映射非空值、遗漏、冲突或跨记录歧义时才调用 Luna。任何值或页面版本变化仍会使旧核验失效。
- 混合保存范围按模块保留已证明的程序核验，其他模块使用一次 Luna 批量核验。完整保存范围及原始资料只传一次，字段 ID 在每个模块内单独映射为短 ID；漏模块、跨模块字段、遗漏字段、拒绝或过期结果仍阻止保存。兼容仅实现单模块 `review` 的模型，但同样不重审已证明模块。
- 经用户确认，经历栏目规划先读取网页实际栏目：网页存在“项目经历”时，项目仍填项目栏目；网页没有项目栏目但存在实习/工作经历时，只有资料记录显式声明 `presentation_policy.user_requested_fallback=无项目经历栏目时按实习填写` 的项目才回退到实习栏目。目前该策略适用于 V次元和国家智慧教育平台，并与腾讯实习按开始时间倒序规划。来源仍分别绑定 `/projects` 与 `/employment`，不会把项目记录改造成虚假的雇佣关系。

页面栏目发现完成后，宿主用程序入口生成记录规划，再按返回的 `source_collection`、`record_id` 和 `module_type` 建立新增/编辑模块清单：

```powershell
.\run.ps1 application plan-experiences --section internships
# 若页面也有项目经历：追加 --section projects
```
- `agent-report.json` / `agent-report.md` 随 application 状态发布，包含任务状态、例外队列、映射与模型命中、耗时、证据、已确定原因和假设。报告不执行恢复、不自动改代码，也不把未知结果改成失败或成功。控件不受支持时提供后续 agent 处理所需的报告，不绕过原 Edge 主图。

可选真实模型烟测：`python scripts/semantic_smoke.py`，仅使用虚构资料，不打开浏览器。它验证模型实际可调用和结构化语义输出；不证明真实网申完成或速度达标。

这一版将模型输出、流程调度和浏览器执行分开。正常答案取自传入的 JSON；关闭调优时额外允许上述明确标记的程序兜底。模型不能给出选择器、脚本、授权、保存或提交命令。本机已安装的网申 skill 已同步程序优先入口；旧智联项目保持原状。

## 已实现

- 真正的 LangGraph `StateGraph`，SQLite 检查点，固定节点与条件边。
- 模型使用本机 Codex 现有账号；每次映射/核验启动独立的受限文本进程。关闭 shell、Node、浏览器、插件、MCP、子 agent 等工具，不改用户的全局设置。
- 第一次运行先向本地模拟模型端点发送探测请求，检查本机 Codex 实际发出的工具清单必须为 `[]`。工具清单非空就拒绝真实推理。探测按程序设置、Codex 路径/版本/文件时间与大小缓存一天；不使用哈希。这个测试不发起真实模型推理，也不读取个人资料。
- 模型只返回 `field_id`、JSON Pointer `source`、允许的转换和依赖。值和浏览器定位都由程序取得；漏掉字段、任意脚本、额外字段、错误来源、依赖环会被拒绝。
- 通用文本、单选/复选、原生单选/多选、具有明确 ARIA 关联的搜索下拉；按依赖顺序处理联动控件。
- 常见控件动作在一次宿主调用内连续执行，无固定字段数上限，一次派发全部依赖可排序的就绪项。最小填写目标为 `min(本批可填写数, 10)`，可填写数不包含快照中已经匹配的项；仅实际写入并回读匹配的字段计入目标。8 秒改为软切批窗口，达到最小目标后才可因窗口到期切批，不要求恰好填 10 项就停。单项动作仍有时间限制，总任务期限和未知调用保护优先；运行中发现冲突、依赖阻塞或控件失败时不强行凑数。
- 现值冲突不覆盖；写前记录 pending；同一命令重复执行只返回既有回执，不重复点击；崩溃留下的 pending 命令拒绝重放。
- 独立核验绑定模块版本。保存前再核对当前值、必填字段和保存按钮语义，只接受新出现的明确保存结果。
- 不含提交、上传 PDF、任意导航、新建标签页、CUA 或任意 JavaScript 写入动作；自动保存验证允许在新保存提示后刷新同一标签页。

## 执行边界

### 枚举暂缓与证件保密填写

原生下拉和已验证归属的 Element UI 下拉找不到精确标签时，执行器记录实际候选并暂缓该项。Element UI 菜单需成功收起且原值未变，才能继续；关闭调用未结束仍暂停。模块枚举核验只允许模型选择现场候选中的等价标签，保留 JSON 原始答案、字段及观察来源，补填后仍需独立内容核验。候选不等价或不唯一时不猜选。

本机私有配置 `identity_document_fill: true` 表示用户已明确给出的持续授权，不是公开模板默认授权。可信程序只为唯一的身份证号码/证件号码文本框生成不含号码的引用；Edge 执行器临时读取配置中的资料，在空框填写，非空不同值不覆盖。回执仅记录存在/匹配布尔值。密码、验证码、人脸验证与正式提交不在此授权内。

对已结束的部分填写，可用 `application repair --run-dir <原目录> --basis <修正依据>` 进入只读重核对，再继续原主图。需要续期时另给 `--renew-seconds` 和 `--budget-basis`；旧历史保留，未结束调用不可用此入口绕过。

受限的是本程序创建的映射/核验模型进程。当前主 Codex 会话和其他软件仍然可能拥有浏览器工具，本程序不能把整个 Codex 桌面变成安全沙箱。

执行层以 Playwright Session 为统一入口。原生路径通过 Edge CDP 取得已有 `Page`，旧 Codex Browser 宿主路径仍可提供 `tab.playwright`。两种路径复用同一个填写执行器。`browser/playwright_backend.mjs` 不启动浏览器、不选择用户配置、不读取 Cookie，也不导航；`browser/cdp_connector.mjs` 只连接已经开启的 CDP 端口、列出页面并按精确 URL 或页面 ID 选择唯一页面。Python 程序发出结构化命令，可信宿主执行并回传磁盘回执，后续批次由 `host/playwright_driver.js` 连续调度。

SQLite 状态与 writer journal 用于恢复进度，不代表网站具备事务或 exactly-once 能力。工具中断/超时不能证明副作用取消。`needs_reconciliation`、`save_unconfirmed` 不会自动重填或重存。

## 文件职责

| 文件 | 职责 |
|---|---|
| `edge_form_graph/graph.py` | 程序映射、预检、批次、异常调优/兜底、核验、保存的固定顺序 |
| `edge_form_graph/field_mapping.py` | 仅按资料模块及字段同义词查来源；独立于网站控件类型与执行器 |
| `edge_form_graph/execution_policy.py` | 调优开关、兜底计划和无模型执行核对 |
| `browser/fallback_policy.mjs` | 文本提示与首个可用下拉选项规则 |
| `browser/search_select.mjs` | 搜索后等待归属明确、唯一匹配的异步候选；不参与字段取值 |
| `edge_form_graph/contracts.py` | JSON 来源、答案转换、完整覆盖和回执校验 |
| `edge_form_graph/model.py` | 无工具的 Codex JSON 模型调用 |
| `edge_form_graph/isolation.py` | 检查实际模型请求中的工具清单 |
| `browser/playwright_backend.mjs` | 把已登录宿主页或原生 Playwright Page 包装成统一 Session |
| `browser/cdp_connector.mjs` | 连接已有 Edge CDP、只读列页、唯一选页并建立原生 Session |
| `browser/playwright_executor.mjs` | Playwright 主执行入口，读取、填写、回读和保存核验 |
| `browser/edge_executor.mjs` | 兼容执行实现；由 Playwright 主入口复用 |
| `host/playwright_driver.js` | Playwright 宿主中自动循环批次，不逐字段调用模型 |
| `host/codex_driver.js` | 旧 Codex Edge 宿主入口，保留兼容性 |
| `edge_form_graph/cli.py` | 启动、恢复、状态、SQLite 与操作包导出 |

## 当前机器运行

`run.ps1` 使用显式的 JOB_APPLICATION_PYTHON 或本目录 .venv，不回退另一台电脑的 Python 路径。其他机器建立独立 .venv 后 pip install -e .。

### 原生 Edge CDP

首次安装 Node 连接依赖：

```powershell
npm install
```

新版 Edge 必须为远程调试使用非默认用户目录。端口按本机 config.local.json 设置；示例使用 9333，不抢占其他应用的调试端口。启动独立 Edge：

```powershell
Start-Process 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' -ArgumentList @(
  ('--user-data-dir=' + (Join-Path $env:LOCALAPPDATA 'Edge-Automation-CDP')),
  '--remote-debugging-port=9333',
  '--remote-debugging-address=127.0.0.1',
  '--no-first-run',
  'about:blank'
)
```

这个独立用户目录首次需要人工登录招聘网站一次，后续启动会保留该目录中的登录状态。程序不复制普通 Edge 的 Cookie，也不读取 Cookie。只读检查当前页面：

```powershell
node scripts/inspect_edge_cdp.mjs --endpoint http://127.0.0.1:9333
```

只有一个页面时会自动选中；存在多个页面时命令列出 `pageId`、标题和 URL 并以 `cdp_page_selection_ambiguous` 退出，不猜标签页。随后用页面 ID 或精确 URL 重试：

```powershell
node scripts/inspect_edge_cdp.mjs --endpoint http://127.0.0.1:9333 --page-id TARGET_PAGE_ID
node scripts/inspect_edge_cdp.mjs --endpoint http://127.0.0.1:9333 --url 'EXACT_TARGET_URL'
```

成功输出中的 `transport` 应为 `native`。检查脚本只读取页面标题、URL 和 CDP 页面身份；不导航、不填写。结束时仅断开 Playwright，Edge 和调试端口继续运行。原生浏览器传输由此不再依赖 Codex Edge 插件；上层编排仍通过同一个结构化命令与回执协议运行。

### 执行已有页面

可信 Node 宿主用精确 URL 或上一步得到的 `pageId` 建立原生 Session，再交给同一个 `playwrightExecutor`：

```js
const cdpConnector = await import('file:///ABSOLUTE_PROJECT_PATH/browser/cdp_connector.mjs');
const playwrightExecutor = await import('file:///ABSOLUTE_PROJECT_PATH/browser/playwright_executor.mjs');
const cdpConnection = await cdpConnector.connectEdgeCdp({
  endpoint: 'http://127.0.0.1:9333',
  pageId: 'TARGET_PAGE_ID'
});
const playwrightSession = cdpConnection.session;
```

任务完成后调用 `await cdpConnection.closeConnection()`；现场验证确认这只断开 Playwright，9333 和 Edge 页面仍存在。

以下是旧 hosted Session 的兼容流程：

1. 按已安装 Browser skill 连接 **Edge**，查找并 claim 原有目标标签页，读取当前接口文档。复用 `edge`、`resumeTab`；不要新建页面来替代已有草稿。
2. 在目标模块已经可见时，依据实际 DOM 确定唯一模块范围；页面全局搜索框不是简历模块。若需要编辑按钮，先正常展开该模块，再观察。
3. 在官方 Node 工具中导入本项目模块并读取快照（以下路径和模块选择器必须替换为实际值）：

```js
const playwrightBackend = await import('file:///ABSOLUTE_PROJECT_PATH/browser/playwright_backend.mjs');
const playwrightExecutor = await import('file:///ABSOLUTE_PROJECT_PATH/browser/playwright_executor.mjs');
const playwrightSession = playwrightBackend.createHostedPlaywrightSession(edge, resumeTab);
const snapshot = await playwrightExecutor.observeSession(playwrightSession, {
  moduleId: 'education-master',
  moduleSelector: 'OBSERVED_UNIQUE_MODULE_SELECTOR',
  capture: {
    // 保存前必须从当前页面验证这些定位及文案；不猜按钮。
    saveControl: 'OBSERVED_SAVE_BUTTON_SELECTOR',
    savedSignal: {selector: 'OBSERVED_SAVE_SIGNAL_SELECTOR', text: '保存成功'}
  }
});
await playwrightExecutor.saveSnapshot('ABSOLUTE_PRIVATE_SNAPSHOT_PATH', snapshot);
```

4. 启动图（资料入口必须是自己的 JSON；不使用示例冒充真人资料）：

```powershell
.\run.ps1 start --profile 'ABSOLUTE_PROFILE_JSON' --snapshot 'ABSOLUTE_SNAPSHOT_JSON' --run-dir '.\private\run-001'
```

默认只填草稿。有本模块保存授权时，加 `--allow-save`。当前学段 `still_enrolled=true` 且无冲突毕业证事实时，可以通过单向规则 `enrolled_no_diploma` 回答未取得本阶段毕业证；不在读不能反推出已取得毕业证。不把 JSON 空值当成否，不引入其他资料来源。

本机可在忽略提交的 `private/local-config.json` 设置 `profile` 绝对路径；设置后启动可省略 `--profile`，显式参数仍可覆盖。已独立核验的 `verified_draft` 可用 `save --run-dir ... --snapshot ...` 在已有用户授权内进入保存节点：新快照的页面身份、模块及全部字段必须与核验版本相同。卡片式保存可配置 `savedSignal.kind=module_readback`、当前模块 `selector` 和实际编辑表单 `editSelector`；仅在编辑区关闭且所有非空字符串值在模块可见文本中出现时确认，未知结果不自动重试。

5. 在 Node 工具中执行导出的当前批次，再由图接收回执：

```js
nodeRepl.write(await playwrightExecutor.runSessionRequest(playwrightSession, 'ABSOLUTE_RUN_DIRECTORY'));
```

```powershell
.\run.ps1 resume --run-dir '.\private\run-001'
.\run.ps1 status --run-dir '.\private\run-001'
```

上述循环可由 `host/playwright_driver.js` 在 Codex code-mode 中一次启动。它只交接程序生成的 request/receipt，不生成网页动作，也不伪造成功结果。模型映射与独立核验之间可有多个浏览器批次，不增加逐字段模型轮次。

可信宿主从本地文件读取 `host/playwright_driver.js` 后，以 `AsyncFunction('tools', 'notify', 'options', source)` 加载它，传入当前 code-mode 的工具对象与以下选项即可。这里加载的是已审查的程序文件，不能用模型返回的计划内容替代 source。

```js
{
  projectDirectory: 'ABSOLUTE_PROJECT_PATH',
  runDirectory: 'ABSOLUTE_PRIVATE_RUN_DIRECTORY',
  start: true,
  profile: 'ABSOLUTE_PROFILE_JSON',
  snapshot: 'ABSOLUTE_FRESH_SNAPSHOT_JSON',
  allowSave: false,
  sessionBinding: 'playwrightSession',
  executorBinding: 'playwrightExecutor'
}
```

原生 Playwright 由 `connectEdgeCdp()` 连接已经运行且身份明确的 Edge 页面，再调用 `createNativePlaywrightSession(page, identity)`。项目不会自行启动 Edge，也不会尝试从普通 Edge 进程复制登录态。普通 Edge 若没有启用远程调试端口，原生 Playwright 无法事后直接接管；此时只能重启到独立 CDP 用户目录，或继续使用旧 hosted Session。

映射/核验进程使用 low 推理；本机 application 入口将字段语义匹配和资料核验均显式固定为 `gpt-6-luna`，模型不可用时报告失败，不继承主会话模型。历史单模块入口未指定模型时也默认使用 Luna。模型调用耗时分别记入 `mapping_seconds` 和 `review_seconds`，不与浏览器耗时混为一谈。

### Codex 模型工作线程的 WebSocket 超时

如果一次很小的模型请求仍需 100 秒以上，并出现下面的输出，不要先把问题归因于模型推理或 prompt 太大：

```text
Reconnecting... 2/5 (request timed out)
Reconnecting... 3/5 (request timed out)
Reconnecting... 4/5 (request timed out)
Reconnecting... 5/5 (request timed out)
Falling back from WebSockets to HTTPS transport. request timed out
```

这表示初始 Responses WebSocket 请求加四次重试都超时，Codex 最后才改用 HTTPS。`2/5` 到 `5/5` 是四次重连，不是四个模型调用；同一次回退可能同时出现在 warning 和 JSON event 中，因此简单搜索 `falling back` 可能得到两次命中。

没有 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量也不能证明工作线程绕过了代理。Windows 原生 Codex CLI 可以读取系统用户代理。排查时应观察子进程的实际 TCP 连接；本机故障复现中，所有连接都进入 `127.0.0.1:7897`，所以问题不是“Luna 线程没有走 VPN”。公共 WSS 端点经同一代理可以正常握手，当前证据只支持将故障范围限定为 Codex Responses WebSocket 的端点或协议链路，不能继续猜成某个具体代理节点或上游组件故障。

Codex 内置 `openai` provider 是保留项，不能用下面的方式覆盖：

```toml
model_providers.openai.supports_websockets = false
```

本项目为每个受限模型子进程注入独立 provider，让它保留原 ChatGPT 登录和同一 Codex backend，但从一开始使用 HTTPS/SSE：

```toml
model_provider = "openai_http"

[model_providers.openai_http]
name = "OpenAI"
base_url = "https://chatgpt.com/backend-api/codex"
requires_openai_auth = true
supports_websockets = false
```

配置位于 `edge_form_graph/model.py` 的 `worker_args()`，仅作用于本程序创建的 `codex exec` 子进程，不修改用户全局 Codex 配置。程序仍先检查 `codex login status`，并移除 `OPENAI_API_KEY` / `CODEX_API_KEY`，不会因该修复静默切换到 API Key 计费。

修复后应同时检查以下条件：

- 结构化结果正常返回，选择的模型仍是预期模型。
- `websocket_mentions`、`reconnect_events`、`fallback_events` 均为 0。
- 子进程仍连接预期的系统代理，而不是把禁用 WebSocket误当成绕过代理。
- 输入字符数与修复前一致，避免把“减少 prompt”误认为传输修复。

历史本机同一智联映射负载的 input、instructions、schema 和完整 prompt 完全一致，模型阶段由 136.218 秒降至 24.922 秒，减少 81.7%。这个数据用于说明该故障和修复，不代表其他网络、模型或网站上的固定提速比例；历史现场证据属于私有运行资料，未随公开仓库提供。可使用 `python scripts/semantic_smoke.py` 做无浏览器的虚构资料烟测，该命令会调用已登录的 Codex 模型。自定义 provider 字段以 [OpenAI Codex 配置参考](https://developers.openai.com/codex/config-reference) 为准。

`private/` 存放个人资料快照、SQLite、操作包和回执，已加入忽略规则。不要提交这些文件。

## v0.1 支持边界

- 先处理一个已展开且唯一定位的保存模块；整份简历的栏目发现/展开、自动新增或删除经历尚未实现。
- 不明确的下拉、缺少稳定标识的重复记录、树形/虚拟滚动列表、自定义多选的已选值读取、iframe/shadow DOM、其他特殊日期控件会暂缓，不假装支持；已识别 Ant Design 单选组和 Ant Calendar 日期输入（YYYY-MM-DD），普通 Ant 输入填写后失焦提交。
- 联动需要计划提供依赖。父级改变后，程序观察子级重置值；仅对实际观察到的变化更新基线。
- 保存需要已观察到的保存按钮和保存结果信号。按钮写着“保存”本身不能证明网站持久化成功。
- 保存点击已返回、业务结果未确认时，主图会派发一次只读核对；未结束的写调用不自动重放。用户手动处理的旧保存另记恢复证据，不改旧 journal。
- 当前不承诺真实网站提速倍数，测试耗时不作为网页性能指标。

## 顺序主图（application 入口）

`application.py` 是外层 StateGraph，复用原七节点模块子图及其 SQLite 检查点。顺序为进入当前模块 → 读取 → 子图填写/独立核验 → 保存边界 → 推进。模型不能指定下一模块。

- 现场清单 `modules` 按页面渲染顺序记录 `id/selector/page_order/save_scope`；`save_scopes` 单独记录 `id/selector/mode/evidence/capture`。清单由可信宿主按现场证据提供，不是模型随意生成的按钮作用范围；自动发现/展开模块尚未实现。
- 同范围内串行填写，范围末尾并行独立核验，全部通过后统一保存。默认两个、最多三个无工具模型工作线程；已附现场 snapshot 的模块可并行预映射，缓存遇字段身份/现值变化失效。不要为了预映射整页增加首次填写等待。
- `mode=automatic` 使用现场配置的 `capture.savedSignal`：等待区别于当前旧提示的新成功信号，核对整个范围未变化，再刷新原标签页并回读。只有全部模块匹配才能保存确认并激活映射；超时、加载失败、值改变均不学习。当前在吉祥航空 `.autoSave` 提示上进行了真实验证。
- 模块出现 deferred/conflict/unknown 或核验失败就停在该模块；没有自动跳过接口。
- 统一保存前重新核对所有成员模块的完整字段，以及保存范围内是否存在未核验控件；保存未知不推进。运行时只接受图当前 `active_command_id/kind/module/target`，旧批次不能越序执行。
- 新入口启动先扫描指定历史运行根目录的 writer journals；未决操作阻止启动，不通过新目录清零。历史单模块命令仍保留用于旧运行诊断，不能拿它绕开主图。该约束不撤销 Codex 主会话自带工具权限。

```powershell
.\run.ps1 application preflight --prior-root 'ABSOLUTE_PRIOR_APPLICATION_ROOT'
.\run.ps1 application start --manifest 'ABSOLUTE_OBSERVED_MANIFEST' --prior-root 'ABSOLUTE_PRIOR_APPLICATION_ROOT' --run-dir 'ABSOLUTE_RUN_DIRECTORY' --allow-save
.\run.ps1 application resume --run-dir 'ABSOLUTE_RUN_DIRECTORY'
```

资料默认仍取本机 private/local-config.json。宿主循环传 `application:true, manifest, priorRoot`，其余 Edge 句柄设置不变；读取也经同一执行器 `runRequest`。保存组证据不明确时，先补现场证据，不猜测按钮用途。

### 填写恢复

未知 fill 现在可派发最多两次只读值核对，单独记录 `value_check_attempts` 与 `value_comparison`，不再被旧写入恢复次数阻止读取。匹配/未变化/冲突/不可读由程序判断；多选按集合、布尔严格比较，展开中的下拉搜索文字不是选中证据。有终结回执的普通绝对值操作只有未知项全部匹配后才可继续并跳过重填；原值未变仍不允许重试未决写入。原 journal 和 `settled:false` 保留，匹配不证明保存或取消旧请求。

两次只读额度按旧 command_id 分开记账，不清零历史计数。用户明确要求修复后继续时，命令绑定的单次 migration 可以在同一字段仍是原值、非保密普通绝对值操作且无冲突的情况下生成新命令；这是用户授权的有限重新填写，不是远程取消证据。显式 repair 记录每次修正依据与累计次数，不受已经耗尽的自动恢复次数影响；自动恢复仍最多两次。

可直接编辑的 Element 日期输入支持普通 fill 后 Tab 失焦提交并回读；已识别的只读 Element 日期选择器通过年、月、日面板逐级选择并回读。智联结束日期外层为 `apply-form-date-now` 时使用独立的 `element_date_now_picker_v1`：先解除“至今”，再复用同一日期选择与回读契约。新子图/修复重入清除上次语义尝试标记，旧计划的自然语言 deferred 理由也会被新版一次性批量重新评估。

主图新增 `prepare_recovery → reconcile_fill → fill_and_review`：对已结束的未知填写或可重试控件失败，自动在当前模块只读核对，保留旧子图结果与回执，匹配目标的字段由执行器跳过；不重放旧 command。每模块最多自动恢复两次，沿用原截止时间和保存权限。用户改值、字段身份/结构变化、缺失资料或真正未结束的调用不会被自动覆盖。

已停止的历史任务使用 `run.ps1 application recover --run-dir ORIGINAL_RUN_DIRECTORY`，不换目录。该入口从原 writer journal 读取真实回执，不能手动把 settled=false 改成 true。若旧记录缺乏调用结束证据，返回 `recovery_blocked / transport_settlement_required`，不是恢复成功。普通 `resume` 仍只接收当前等待中的回执。

### 保存观察与历史恢复

保存日志分开记录点击派发、点击返回、确认弹窗、观察返回。观察可见 DOM 弹窗、原生弹窗类型、校验错误与加载状态，最长观察 5 秒，不承诺捕捉所有瞬时提示。原生弹窗无可读正文时交接；DOM 确认须配置现场精确文本和按钮，不能泛化点击“确定”。

用户明确已手动处理旧保存时，可用 `application reconcile --journal ABSOLUTE_OLD_JOURNAL --user-saved --basis USER_STATEMENT --run-dir ABSOLUTE_RECOVERY_DIRECTORY` 准备只读命令，再由同一 Edge 执行器执行。仅同目标且原预期值匹配时写独立 resolution。`superseded_by_user` 不是自动保存成功，也不证明旧远程调用结束；原状态完整保留。该恢复命令不点击保存，不能用普通“继续”冒充手动处理依据。

## 验证

```powershell
python -m unittest discover -s tests -v
$nodeTests = Get-ChildItem tests -Filter 'test_*.mjs' | ForEach-Object FullName
node --test $nodeTests
python -m edge_form_graph.isolation
# 可选：使用现有 Codex 登录，对虚构 JSON 做真实模型映射与独立核验；不操作网页。
python -m tests.smoke_model
```

测试区分程序测试、真实模型调用、真实 Edge 只读、真实网页写入。本次公开代码的离线验证范围见 [仓库说明](../README.md)；历史现场验证记录属于私有运行资料，未随公开仓库提供。
# 来源纠正

## 控件例外处理入口

未知文本元素保留为 `unverified_text_candidate`，主填写批次不再默认 fill。
已停止的原申请用 `application inspect-controls --run-dir ...` 获取只读控件证据；经宿主执行和 resume 后，用 `application apply-control --label ... --source /contact/hometown --adapter province_city_dialog_v1 --run-dir ...` 绑定已知适配器。普通文本使用显式 `plain_text_probe_v1`，聚焦后出现弹层即停止，不能强行输入。

成功方法存入 `private/control-methods.json`；相同范围后续可省略 source/adapter。每次重新绑定实际 DOM，原生下拉选项要求唯一匹配。城市选择确认后显示延迟，可在 fresh inspect/resume 后执行 `application reconcile-control`，只读对账不重放确认。原申请旧日志与保存状态不清除。连接编号改变时 `inspect-controls --browser-id ...` 仅允许同一 tab/URL 的只读诊断，不据此迁移原写入身份。

当前是 agent 显式选择方法的第一版，尚未把全部陌生控件的批量方法匹配接成默认自动循环，也没有实现任意新控件代码的自动安全验证。位置与文本适配器通过本地测试不等于整份简历保存成功。

独立核验拒绝的模块可经原申请 `repair` 入口纠正来源：`--correct-field LABEL SOURCE TRANSFORM`（可重复）。字段必须在当前模块唯一且非受保护字段；只允许 JSON 指针与白名单转换，不接受答案字面量或选择器。程序先读取原页面并比较，再编译纠正方案，保留历史回执与旧方案，重新走独立核验及保存边界。`join_location` 仅串接同一来源对象内显式的 province、city、可选 district，不以出生地、户籍或居住地替代籍贯。



## 保存后固化与人工补填导入

定点修复时，原生驱动可设置 `stopAtModuleBoundary: true`，在当前栏目结束后、下一个栏目命令派发前返回。命令与检查点保留，继续执行时仍使用原申请目录。`application revisit-held --reread` 可在 writer 独占锁内替换尚未派发、没有 writer journal 的普通观察命令；旧观察保存在 revisit_history。已派发读取、填写、保存或新增不适用此入口。

Moka 下拉规则同时支持 Select common-item 与 Menu pointer 行；选项内的高亮文字不会被重复计为候选，行内禁用标记也会排除该项。年月子控件通过 `.month-range-select` 中的两项或四项结构维持身份，选择年份后提示文字消失不会把月份误认成完整日期输入。

固定流程由主图执行：启动后重新读取当前 DOM → 用资料库规范值程序核对当前页面 → `known_plan` 命中映射并执行 → `match_unknown` / `review_enums` 集中处理剩余问题 → 独立核验 → 保存及回读 → `compile_experience`。程序核对把字段分为正确、空白和错误三类：正确值记为 `already_matched` 并跳过，空白值和错误值都进入填写；无法唯一绑定资料来源的字段才进入语义处理。草稿、部分成功、未知写入、保存未确认和过期审核均不激活规则；知识写入失败只报错，不重放网站保存。

网页已有非空值不会直接视为正确。稳定 `record_id` 已绑定时，程序先按字段标签和白名单转换生成普通映射操作，再比较当前值与资料库规范值：一致由执行器记为 `already_matched`，不一致生成纠正写入；排名先用同一记录的 `rank_position/rank_total` 选择页面实际提供的最小覆盖档位，缺少数值时才用明确 `rank_band` 上界匹配真实选项；完整学位与证明人关系只使用受限转换。无法程序绑定的非空字段才进入 Luna，模型仍不能提供答案字面量或浏览器动作。保存并回读后，这些普通映射与枚举关系继续通过 `compile_experience` 写入正式知识库。

字段库保存来源与转换，不缓存个人答案或选择器。单选组按真实选项标签匹配，`present_absent` 将明确布尔值映射为有/无；`yes_no` 映射为是/否。重复实习按模块的稳定 record_id 解析，公司专属记录通过 company_answers 的稳定 record_id 解析。条件规则每次重新检查；重新学习不得丢弃原条件。

用户手工补填的已有答案可通过 `scripts/import_reviewed_mappings.py` 导入同一个 `private/form-knowledge.json`。必须提供 canonical profile、来源规则、实际观察、独立审阅、保存后完整回读证据。脚本只导入审阅过的字段，随后直接调用 `known_plan` 验证能否复用，报告语义模型调用数。该导入不是把整页标记为已完成，也不写网站。

异常固化有两种产物：字段/枚举问题进入映射库；控件实现问题由 agent 修改受限执行器，经检查和真实控件证据后复用。模型返回的任意脚本不会自动进入生产执行路径。


### 未知控件处理与复用（2026-09-27）

联动处理（2026-09-28）：选择控件完成后有界等待当前模块变化，比较字段增删、值、选项及禁用状态；发现变化就结束批次并重新生成受影响字段的计划。`edge_form_graph/linkage.py` 将初始字段计为第 1 层，新出现或被联动改变的子字段为父层 +1，上限 `MAX_LINKAGE_DEPTH = 15`。这不是整页扫描 15 次的限制；同层字段不累加。第 16 层或重复状态的循环分支不再写入，其他就绪字段继续处理，报告保留阻塞原因。重新规划保留仍然匹配的已填值；网站禁用且非空的计算字段不再尝试输入，也不固化成个人事实。

原生 Playwright 驱动默认开启 `--control-agent on`，可以独立用 `--control-agent off` 关闭。它与 `--agent-tuning` 分开：后者控制字段答案的语义处理，前者只处理操作方法。两者都关才完全不调用模型。

程序停止于控件异常时，先通过原执行器重新读取当前模块，再查询 `private/control-recipes.json`。已验证结构命中直接复用；未命中时调用 `gpt-6-luna`，只发送字段标识、标签、控件结构和允许的方法列表，不发送整份简历或字段答案。来源仍由程序从映射库和当前 `record_id` 解析。模型返回的未知方法进入 `handler_implementation_required` 队列，由开发 Agent 补充识别、执行与验证代码；不会直接运行模型返回的任意脚本。

已有方法经同一主图的 `apply-control` 和同一 Playwright 会话执行。调用已结束、控件回读成功且 `committed=true` 才写入操作库；候选选择不算学习成功。库中只存结构、适配器名称和验证命令编号，不存个人答案、数组下标或 DOM 选择器。每次执行仍现场检查结构。控件成功后重新读取当前模块再继续；保存未确认或调用未结束时不借此重放动作。

每次运行最多三轮异常处理，同一标签、来源、方法不重复派发。紧凑请求及决定保存在运行目录的 `control-agent/`，区分 `gpt-6-luna` 与 `program_recipe`。完全新增的操作实现仍需要开发 Agent 处理，此入口不是通用的无人监督代码生成器。

首个现场验证案例是 Alibaba/Fusion 的月份区间：识别 `.next-range-picker` 双端点，打开所属弹层，填写两个 `YYYY-MM` 输入，确认后同时回读两端。弹层可能被限制在视口内并覆盖触发器，因此以唯一展开的所属控件和弹层结构核验归属，不能假设固定上下间距。正确日期按月份核对后跳过；日历不会执行“下拉选第一个”的兜底。控件回读成功与整个简历已保存是不同状态。

### 控件判断模块边界

`browser/rules/` 独立承载现有控件扫描及识别规则，`browser/control_detection.mjs` 仅作兼容导出：

- `readModuleDOM(options)`：读取模块字段、结构分类、值的可读性及保存按钮证据。
- `readControlEvidence(options)`：读取触发器、菜单和弹窗结构，不执行点击或填写。
- `classifyControl(field)`：根据已观察类型匹配已有操作模式；未知返回 `agent_required`。
- `classifyDialog(evidence)`：匹配现有省市弹窗规则；未匹配返回 `null`。

规则库不加载资料库、映射库或模型，不连接浏览器，不执行填写。执行器和控件库单向依赖它；旧导出仅作兼容转发。DOM 读取函数会被浏览器 `evaluate` 序列化，必须保持函数体自包含。修改识别规则在 `browser/rules/` 及 `tests/test_control_detection.mjs`、`tests/test_value_readback_dom.mjs` 中完成。

这是现有行为的模块化提取。控件识别规则已经独立，执行入口已经迁移到 Playwright Session；当前已登录 Edge 已通过 CDP 使用原生 Playwright Page，旧 hosted Playwright 仅保留兼容。现有省市弹窗仍使用标题及省/市选项识别，尚未泛化为任意层级选择器。识别结果不证明可安全写入；动作前检查、选中状态回读和保存确认仍由执行模块负责。

### 开启调优后的混合字段与保存恢复

同模块可能同时存在程序已找到来源的字段和模型确认缺少资料的字段。只要任一方产生了新映射，主图必须继续执行那些就绪动作；不能因为模型没有新增映射就直接进入复核。`tests/test_program_first.py` 覆盖这一漏填回归。学历与学位分别读取 `education_level`、`degree`；重复集合达到容量、添加按钮消失时，仍按行内字段结构识别集合。

保存错误也可能显示在表单外的 Fusion 提示中。保存观察器读取这类校验提示，但不会重复点击未知结果的保存。原生 Playwright 的 `browser/persisted_readback.mjs` 提供一个限定于阿里教育栏的只读回查：沿用页面已使用的详情查询及会话，认证数据不离开页面，只返回教育记录条数。原保存调用已结束、草稿仍与原回执匹配、同目标服务端明确没有教育记录时，历史预检允许修复当前草稿；这不等于原保存成功。读取失败、非空结果、目标变化或未结束的写入仍保持阻塞，旧日志不删除。该站点详情查询使用 POST，适配器不会调用保存接口。

## 控件能力契约（2026-09-28）

### 连续填写与正式投递（2026-09-30）

`node scripts/run_mass_apply_batch.mjs QUEUE_JSON OUTPUT_DIRECTORY` 调度独立页面，每页保留原 run 和 writer journal；`resume:true` 继续原 run。未配置正式提交来源的条目只统计填写/保存，不能当投递数。条目的 `submission` 配置提供 `job`、`jobEvidence`、`historyEvidence` 和 `ledger` 后，完整范围通过才进入独立审查、预览、最终提交和成功复核；验证码或未知结果立即返回并处理下一家公司。

原生驱动在每个页面复用一个顺序 Python 宿主，减少逐命令重启和重复导入。每条命令仍重新读取原检查点、配置与资料，并沿用原独占锁；异常、超时和断线不会自动重放。必填空字段经语义判为资料缺失/冲突，或已核对枚举没有等价选项时，驱动返回 `queue_disposition: skipped_missing_required_answer`，保留原草稿与 journals，批次继续下一家公司。可选未知字段、尚未完成的浏览器调用和未核对的控件错误不能冒充这种已确认缺答案。

`scripts/review_application_submission.py` 提供按官方岗位、当前账号历史、完整当前表单与原始资料进行独立 Luna 审查的通用入口。申请表阶段和确认预览阶段分别审查；历史修改日志不重复塞入审查输入，原预览值及来源仍完整保留。审批结果不会自行点击网页，正式派发仍由下面的提交入口执行。

`node scripts/run_reviewed_submission.mjs CONFIG_JSON` 连续执行已完成表单的上述正式提交流程；已派发阶段只读回查，不重复点击。原网站成功文案先现场定位，再交独立 Luna 核验，经过原 supervisor 确认才写成功台账。重复读取已有成功记录不会增加 `new_submissions`，成功页面确认后关闭。当前连续适配覆盖已观察的 Moka 预览、确认和成功文案；其他网站保留为待适配。

正式提交使用 `node scripts/run_edge_submission.mjs capture PAGE_ID RUN_DIRECTORY EVIDENCE_JSON` 捕获当前完整申请；独立 `gpt-6-luna` 核验原始资料、官方岗位资格、账号投递历史和实际字段后，生成绑定快照的 plan。`dispatch PAGE_ID RUN_DIRECTORY PLAN_JSON` 在派发前落盘并保留阶段锁。需要预览确认的网站分别使用 `phase:submit` 和 `phase:confirm`，最终按钮必须现场观察且单独复核。首次未知结果可使用 `outcome_mode:independent_observation`，不猜成功文案；实际返回页经独立复核后通过 `accept-outcome PAGE_ID RUN_DIRECTORY REVIEW_JSON` 记录确认页或明确成功。滑块留待本人完成，`reconcile PAGE_ID RUN_DIRECTORY confirm` 只读核对，不重复点击。

只有 `blocked_before_dispatch` 且不存在派发记录或点击结果的预检失败，才能用 `release-preflight PAGE_ID RUN_DIRECTORY PHASE` 释放预留锁；旧失败日志保留。已派发或未知结果无法释放该锁。只有真实网站成功证据才计入新投递数。

飞书招聘的空白经历卡片支持程序添加和数量回读。修复识别后可用 `capture_edge_inventory.mjs PAGE_ID INVENTORY_JSON` 只读捕获，再沿原 run 使用 `application_cli extend-inventory` 扩展尚未写入的新栏目。已有填写、经历新增或保存未知先恢复原记录，不能重新 start。纯模型复核节点可使用受限的 `resume-pure-review`，驱动自动恢复该节点，不重放浏览器动作。

识别规则库 `browser/rules/`、控件库 `browser/controls/`、注册表 `edge_form_graph/control_catalog.json` 已分离。现有 15 个执行入口由注册表关联，JS 导入表自动生成。控件目标、执行回执、核验跳过及新增驱动流程见 [控件能力契约](control-contract.md)。本节中的最新用户规则优先于旧版保密值路径及必须完整回读的描述。

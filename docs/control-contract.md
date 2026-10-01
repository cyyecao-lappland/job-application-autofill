# 控件能力契约与扩展

本分支将识别规则库、控件库和注册表接入原有 Python 主流程与 Playwright 执行器。保留原有操作日志、旧命令读取和未决调用恢复；不更换浏览器后端。

## 本轮已落实的用户决定

- 注册表不用哈希。启动时 Python 检查实际加载的 JS 驱动报告；协议、注册表版本和声明逐项一致才开始执行。
- 目标值、实际值采用普通数据路径。个人字段不再因隐私分类被排除，不新增保密 token、加密存储或值引用层。历史 `secret_ref` 命令保留兼容执行，不新建这类操作。
- 不能完整回读时标记 `verification_skipped`，不重填，不因此阻塞后续字段及模块；不能将其统计为匹配、写入核验成功或学习证据。
- 完整回读发现差异仍属异常；调用超时、在途调用、未知保存不会被降级为“核验跳过”。

## 单一声明与实际运行版本

唯一人工能力清单：`edge_form_graph/control_catalog.json`，随 Python 包安装。

生成命令：`node scripts/build_control_registry.mjs`。

生成一致性检查：`node scripts/build_control_registry.mjs --check`。检查直接比较生成文本，不计算校验和。

JS 静态导入表：`browser/controls/registry.generated.mjs`。驱动本身导出 `declaration`，运行时报告已加载实现的声明，不能仅重读 catalog 来伪造一致。

两端比较：`protocolVersion`、`registryVersion`，以及按名称对齐的 `name / adapterVersion / targetTypes / protocolVersion`。目标类型按集合比较；重复名称或类型无效。错误码 `CONTROL_REGISTRY_MISMATCH` 附具体差异，写入前停止。

新请求还携带 Python 注册表报告；执行器在派发前复核。新回执携带实际 JS 报告，Python 消费时复核。历史无报告的请求仍能走显式兼容入口，但不会被描述为已经完成新握手；默认原生宿主的启动握手是强制的。

## 目标与回执

Python 的 `control_contract.py` 根据声明的目标类别编译 `controlTarget`，字段来源仍由资料映射负责。支持文本、地区层级、日期、日期区间和单选目标。月精度保持月精度，不制造具体日期；缺少结束日期不等于至今。

统一服务 `browser/controls/service.mjs` 调用 `detect → prepare → apply → read → compare`，返回：

```json
{
  "schema": "control-receipt/v1",
  "call": "finished",
  "verification": "skipped",
  "committed": false,
  "reason": "readback_incomplete",
  "persistence": "not_assessed"
}
```

`match`、`skipped` 和 `mismatch` 分别表示匹配、核验跳过和真实差异。公共写入日志仍记录传输与动作是否终结；抛出的传输错误不能转换为上述跳过结果。

`verification_skipped` 是调度终态，不是成功证据。后续若获得完整且不同的实际值，应报告差异。跳过只在当前目标、模块、控件身份与已结束的原操作一致时沿用，不跨申请或记录迁移。学习阶段排除这些字段及控件方法。

## 三个维护入口

| 部分 | 位置 | 职责 |
| --- | --- | --- |
| 规则库 | `browser/rules/` | DOM 结构观察、弹层归属、控件类别识别及匹配条件；不填写、不读取申请人资料 |
| 控件库 | `browser/controls/adapters/` | 接收当前控件和目标信息，执行填写、选择、上传、日期和地区操作 |
| 注册表 | `edge_form_graph/control_catalog.json` | 关联规则导出与控件导出，登记名称、版本、目标类型；不存识别谓词或操作代码 |

规则库的 `dom.mjs` 负责现有页面扫描，`recognition.mjs` 负责现有分类和路由规则，`popup.mjs` 负责弹层归属和选择证据，`aria_search.mjs` 负责 ARIA 搜索列表观察。浏览器会序列化 DOM 读取函数，所以它们的函数体必须自包含。规则消费新观察的结构，定位信息由当次快照提供，不持久保存旧节点。

注册表中的 `rules` 可以包含多条规则，指向同一个控件实现。多个不同控件在同一入口命中时返回 `AMBIGUOUS_ADAPTER`；无匹配时进入原有缺失能力路径。注册生成器只生成导入及关联，不能将识别逻辑复制回注册表。

当前保留两种已有调用协议：

- `control` 路由进入原有标准目标服务，驱动使用 `detect / prepare / apply / read / compare`。
- `field` 路由接收主流程已绑定的字段和值。普通字段驱动提供 `applyField`，复用公共身份检查、回读、比较及操作日志；已有日期驱动也可由此路由进入标准目标服务。
- `manual` 标识需完整上下文的显式控件入口。地区和成对日期继续由原有异常处理流程绑定目标，不因目录迁移就自动填入不完整目标。

这些是兼容现有执行语义的入口，不是独立的多份注册表。Python/JavaScript 启动握手检查所有已注册执行入口；显式控件命令只列出能接收标准目标的驱动。

## 已迁移控件

共 15 个注册执行入口，包含此前 7 个显式适配器，以及原先写在执行器里的 8 类操作：

| 实现文件 | 控件能力 |
| --- | --- |
| `adapters/text.mjs` | 普通文本焦点探测与填写 |
| `adapters/native.mjs` | 已识别文本、复选框（含 Next）、单选、单选组、文件上传、原生下拉（含多选） |
| `adapters/combobox.mjs` | Element、Next、ARIA 等已有下拉路径，含搜索及 Next 标签选择 |
| `adapters/aria_search.mjs` | 归属明确的 ARIA 单选搜索 |
| `adapters/ant_date.mjs` | Ant 日期输入 |
| `adapters/element_date.mjs` | Element 日期面板 |
| `adapters/element_date_now.mjs` | Element 日期／至今包装控件 |
| `adapters/next_range_date.mjs` | Next 年月范围及至今 |
| `adapters/region.mjs` | 双原生下拉省市弹窗 |
| `adapters/administrative_region.mjs` | 已知行政区划搜索弹窗 |

`browser/control_detection.mjs`、`browser/control_adapters.mjs`、`browser/search_select.mjs` 只保留兼容导出，旧调用仍使用同一份实现。`legacy_bridge.mjs` 只保留旧值格式转换和兼容导出。普通控件操作不再写在 `edge_executor.mjs` 的逐类型分支中；执行器仍负责调度、身份与原值检查、日志、核验和网站保存。历史 `secret_ref` 特殊命令继续兼容，不新增此路径。

## 扩展步骤

1. 在规则库新增识别函数；已有观察证据足够时只需返回匹配与否，否则在规则库补充相应结构观察。
2. 在控件库实现操作。标准目标用五阶段接口；普通字段操作可复用公共核验，提供 `applyField(ctx)`。不查询资料、不决定模块保存。
3. 在注册表登记实现及规则入口，运行生成器。新增同类控件不修改主流程或增加名称分支。

例如，新增普通字段驱动时，在声明中填写：

```json
{
  "name": "example_choice_v1",
  "adapterVersion": 1,
  "protocolVersion": 1,
  "targetTypes": ["choice"],
  "execution": "field",
  "implementation": "./adapters/example.mjs",
  "export": "driver",
  "rules": [
    {"implementation": "../rules/example.mjs", "export": "matches", "route": "field"}
  ]
}
```

驱动导出同名、同版本的 `declaration`，修改注册集合或关联时更新 `registryVersion`，修改执行契约时更新 `adapterVersion`。生成及检查命令仍是 `node scripts/build_control_registry.mjs` 和其 `--check` 形式。

已有控件换页面结构，只补规则；操作逻辑变化，只改实现；全新控件改以上三处。`private/control-recipes.json` 是已验证结构到已有驱动的复用记录，不是第四个手工注册入口。新增全新目标类型时才需要扩展通用目标协议。完成后运行受影响的测试，再在已授权真实页面验证。

新增 `aria_search_select_v1` 演示了扩展：支持可编辑 `role=combobox`、单一 `aria-controls` 关联的单选 listbox、可观察异步结果和明确关闭状态；完整提交值通过 `aria-valuetext` 回读。缺少该实际值时标记核验跳过。虚拟列表、多选、无关联弹层、未知查询刷新机制不在当前驱动支持范围内。

旧结构方法库仍可读取。v2 方法身份排除瞬时禁用／只读状态，保留组件及交互特征，记录适配器和协议版本；执行前仍重新核对现场。旧 journal 不重写，不靠新建目录清除 unknown。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -q
node --test tests/test_*.mjs
node scripts/build_control_registry.mjs --check
node scripts/test_control_browser.mjs
```

最后一个脚本启动独立的无界面 Edge，只使用本地合成 HTML，不访问用户正在填写的页面。它是浏览器行为验证，不是招聘站点验收，也不是独立制作的跨站留出集。

2026-09-28 首轮契约实现：Python 263 项、Node 171 项通过。随后三库迁移只运行针对性回归：Python 控件相关 28 项、Node 相关 130 项通过；本地 Edge 合成场景 58 项：52 match、2 skipped、4 正确拒绝。此次未重新运行全量测试，也未验证真实招聘网站及真实保存／重载；没有测得性能提升。

## 兼容与回退

只在没有在途写入的边界切换版本。保留原运行目录及 journal；旧版本不能理解新回执时先只读检查，不重放操作。本分支基于用户已有未提交改动创建，不应通过 `git reset --hard` 回退整个工作区；应按文件审查本次修改。

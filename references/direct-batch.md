# direct-tool 有界批执行

## 范围与入口

`scripts/direct_batch.js` 导出自包含 `createDirectRuntime()`，返回 `executeBatch`、`nextAction`、`signature`、`capable`。`scripts/direct_playwright.js` 提供真实 Playwright Page 连接层，不连接或启动浏览器；page 必须来自当前已授权且受支持的宿主。没有网站模板或固定选择器。

先检查当前浏览器工具文档。支持一次 JavaScript 调用中等待多个动作时，在同一次工具调用中调用 executeBatch；支持本地模块加载就 require 文件，否则把这两个工厂的实际源码作为普通 JavaScript 声明放入受支持的工具运行环境。不能将整个执行器放进仅允许只读 DOM 的 page.evaluate；evaluate 只用于 DOM 读取，写入通过 fill/check/selectOption。不能安装桥接服务或绕过宿主能力限制。若宿主仅提供分离的单动作接口，继续有界 slow 操作并报告“该通道未实现跨字段工具调用合并”。

标准 Playwright port 首批支持 text、textarea、radio=true、native select；自定义下拉、日期、上传仍走 slow。其他已验证方法可实现同一 host 接口，但不得凭字段名字宣布支持。此分支的本地测试不是任何真人网站的验收或性能基线。

## 单次实际调用

```javascript
const {createDirectRuntime} = require(skillScripts + '/direct_batch.js');
const {createPlaywrightHost} = require(skillScripts + '/direct_playwright.js');
const runtime = createDirectRuntime();
const host = createPlaywrightHost(page, identifyExistingTarget, capabilities, structureConfig);
const result = await runtime.executeBatch(plan, preflight, host, executionState, {
  callKey: currentCallKey, maxOperations: 30, maxBatchTime: 8000
});
```

`identifyExistingTarget` 必须从当前受支持宿主读取 browser/tabId/url，与现有发现和 probe 的 target 完全一致，不能固定返回计划目标。capabilities 来自本次勘察与方法验证，按操作 ID 记录 `{ready, verified, structureStable, recordRef?, neededForSave?}`。

每条 native 操作的 locator 为运行时实际观察的 `{selector, recordSelector?, recordAttribute?, recordRef?, anchorSelector?}`。重复记录必须提供唯一 recordSelector、稳定属性和值，以及指向锚点字段的 anchorSelector；port 同时检查稳定 ID 和 op.anchor.value。没有稳定记录身份就留在 slow，不用卡片序号冒充。结构配置 `{records:[{selector,identityAttribute,sectionId}], sections:[{id,selector}], stepSelector?}` 同样来自页面观察。

port 检查原生元素真实类型、可见性、值与唯一性，每次重新定位。未知控件类型不能冒充 text。selectedValue 使用实际已选标签，不使用搜索输入。结构观察不读取证件值；其他页面快照仍须在读取前排除敏感值。

host 接口为 `identify(options)`、`structure(options)`、`inspect(op,options)`、`write(op,options)` 和 capabilities。options.timeoutMs 为当前剩余预算，options.deadline 为原始绝对截止时间。host 不得在内部重新起算完整预算；真正发出写动作前再次检查 deadline。inspect 返回 count/kind/readStatus/value/recordRef/anchorMatched；write 返回 written+settled:true，或 failed+noEffect:true+settled:true，其他情况保持未知。异常可携带 requestId/callId 与 settled；缺少明确结束证据按未决处理。

## 外层状态与恢复

使用现有私有执行状态和可靠落盘方式，不改变 adapter checkpoint：

1. 新运行开始就记录 metrics.run_start，不能到首批派发才开始计时。发现/探测/核验浏览器调用完成后用 direct_metrics.recordObservation 登记真实回执及 wall time。
2. 预检通过后 `beginCall(state, callKey, dispatchIds)`，可靠落盘，再把该状态与相同 callKey 传给执行器。当前 prepared 调用可执行一次；旧调用、running 调用或其他 pending 一律拒绝。
3. 实际工具返回后，以真实 callId 和 settled 状态调用 finishCall，再落盘。不要用自造工具回执。外层超时则保持 pending；不能因为本地计时器到期就认定远程取消。
4. 返回 unknown 或 pendingRemote 时仍阻塞下一批。由原通道核实远程结束，再只读核对受影响字段、记录与用户修改，持久记录恢复证据后解除 pending。不能重用原 callKey 重放。
5. 调用未结束前不换通道、不发起新的浏览器读写。executeBatch 的计时 race 只结束等待并保留未决，不保证宿主请求取消。

结果分类为 written、alreadyMatched、conflict、failed、unknown、unattempted，分别保留，不将返回 written 的动作直接当保存。普通字段恢复按 target/before/其他值判断；曾确认完成又被修改时保留用户值。新增/删除记录、保存、跳转保持独立 durable checkpoint。

## 调度与增量发现

每次实际回执及局部重新观察后调用 `nextAction(plan,state,capabilities)`。它只产生下一项建议，不自行操作浏览器：

- 未决调用优先阻塞；已完成且独立内容核验通过、必填满足、revision 与 reviewedRevision 一致的独立模块，在保存授权内优先保存。
- 有 ready Fast 就派发短批次；无 Fast 时优先能解锁更多直接依赖的 Slow，其次保存所需 Slow。
- 新增记录单独执行，重新发现唯一记录后更新 results.recordRef 与 probe，再派发子字段。计划里存在 dependsOn 不等于依赖完成。普通字段依赖可在同一批前序完成，但执行前仍回读依赖结果；产生结构变化立即停止。
- 没有可执行项时返回 deferred，仍保留完整计划。后续取得方法证据可以再入队，不修改自动意图为 manual。

结构投影直接比较 route/step/modal、section IDs、record IDs、field keys、kind、visible、required、enabled，不做哈希。计数器、颜色、普通校验提示不触发重发现。原生 port 观察当前文档，不穿透 iframe/shadow root；这些区域须单独勘察并标记覆盖未知，不承诺一次发现整页。

每批默认最多 30 次字段处理或 8 秒；提前遇到定位失效、依赖/记录变化、语义结构变化或未知写入即返回。额度可按宿主调整，但仍受原始申请总预算限制。动作尚未结束时不得继续派发。局部结构变化只重新发现受影响区域；route/step 改变按模块重发现。

## 最小指标与验证

summary 返回 TTFF、总耗时、browser_tool_calls、batch_count、batch_duration、successful_writes、alreadyMatched、failed、conflict、unknown、deferred、manual 和每次调用成功字段数。状态收敛时把最终 deferred/manual 写回 results，run_end 在真实结束时记录；不能把未派发项排除后声称完整完成。仅保留字段 ID 和计时，不记录个人值。

先记录旧逻辑的真实基线，再启用新路径。没有真实页面运行时只报告本地行为测试通过，不报告提速倍数、P95 或 200 字段完成时间。测试：

```text
python -m unittest discover -s scripts -p "test_*.py"
node --test scripts/test_direct_batch.js scripts/test_direct_playwright.js
node scripts/test_bounded_batch.js
node scripts/test_execute_adapter.js
```

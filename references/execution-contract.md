# 执行与恢复

这套工具分离计划检查、真实调用和结果记录。direct-tool 可直接使用当前浏览器工具；adapter 才加载本地执行循环。没有实际驱动时不得写空壳文件并宣称执行完成。

## 最小计划与探测

沿用 [plan-schema.md](plan-schema.md) 的 inventory、coverage、operations 和 before 快照。首次创建本轮目录；后续小批次继续使用同一计划、授权和状态，不清空已完成项。before 保留原计划的旧值基线，probe 才记录当前值；已匹配目标允许 probe 返回目标值，以便恢复时跳过，不把已完成项误判成预检失败。

`execution` 示例：
```json
{
  "authorization":{"fill":true,"save":true,"basis":"用户要求填写并暂存，不提交"},
  "roundStartedAt":1788840000000,
  "budgetMs":1800000,"operationTimeoutMs":120000,"saveTimeoutMs":180000,
  "connectionAttempts":2,"familyFailures":2,
  "driver":{"mode":"direct-tool","name":"当前实际浏览器工具"},
  "target":{"browser":"现场浏览器标识","url":"https://example.test/form"},
  "dispatchIds":["F1"],
  "probe":{
    "target":{"browser":"现场浏览器标识","url":"https://example.test/form"},
    "callId":"真实只读调用编号","settled":true,"observedAt":1788840000000,
    "operations":[{"id":"F1","count":1,"kind":"text","value":"","anchorMatched":true}]
  }
}
```
示例时间仅说明格式，必须用实际开始/观察时间。probe 记录最近五分钟内真实、已结束的只读调用；不能填写期望值冒充实际值。`dispatchIds` 只列本批次，不能用一个字段的探测担保其他未观察字段。新增记录依赖的字段先不列入 dispatchIds，创建后观察当前模板并更新下一批探测。

运行 `python scripts/preflight.py --run <run>`。输入必需文件为 `alignment-plan.json` 和 `before.json`；direct-tool 不要求 batch.js 或 handoff.md。adapter 模式另有真实 batch.js、`driver.entryPoint` 和 `probe.entryPoint`，探测必须实际经过该入口的读取方法。

`planReady` 只表示本地结构检查通过；`ready` 还要求本批执行方式和定位证据齐备。备注会明确：这些是调用者提交的证据，检查器不访问浏览器、不授予权限、不证明事实来源内容，也不证明实际执行。全手动计划 planReady 可以为真，ready 必须为假；不是新的审批点。

每个自动目标包含 action、id、label、before、value、source、kind、family、path、locator、methodEvidence。kind 支持 text/radio/select/date/cascade/search/multi/editor/file/captcha/record；复杂控件需本站 savedMethodEvidence。无 JD 用 unavailable/reason、unranked 和已有 generalProfileBasis，不编造 JD，也不重复询问已明确的网申范围。

来源声明必须指向真实 JSON 记录和字段，预检尚不解析任意自然语言 source。不能把非空 source 当作事实核验。inventory 从完整 JSON 建立，不能只列模型选中的少量字段。

## direct-tool：实际调用优先

1. 按真实接口观察当前批次并记录 probe；本地检查通过后，立即执行相同目标的短调用。
2. 保存实际发给工具的代码/动作和工具返回编号、阶段、结果。不要另写一份没有被调用的 batch.js 来替代实际代码。工具日志已有原文时引用其真实编号，避免重复复制个人值。
3. 每个小批次返回后及时更新 execution-state.json；写入前先记 pending。若当前环境只能在外层工具间落盘，就诚实记录这一粒度；中途未返回的调用属于未知，不能声称每个内部步骤都有持久检查点。
4. 工具会话恢复后按 SKILL.md 的网页核对规则继续。原授权有效，稳定结果保留；不借新 run 清零未知动作或预算。

此模式由模型调用工具，不会自动受到 bounded_batch.js 的拦截。没有实际执行调用时，只能报告准备完成。不得声称“调用了执行器”，除非日志中有真实入口调用和返回。

## adapter：可加载代码时的执行入口

支持 Node 本地加载的 host 使用 `scripts/execute_adapter.js` 的真实入口；它实际加载导出的工厂函数，拒绝注释/动作清单冒充函数，提供文件锁与原子状态落盘。工厂 `createAdapter(host)` 必须在构造阶段无网页副作用，只返回方法。host 由当前受支持的浏览器连接提供，入口不会创建或接管浏览器：

```javascript
const {probeAdapter, executeAdapter} = require(skillScripts + "/execute_adapter.js");
// 准备时实际读取目标。返回后从真实外层工具结果补 callId，更新 probe 并执行预检。
const observed = await probeAdapter({runDirectory, host, operationIds: ["F1"]});
// 下次实际调用：读取该目录的计划、预检、batch.js 和原状态，真正执行。
const result = await executeAdapter({runDirectory, host, options: {maxOperations: 4}});
```

这两步属于不同实际调用，不能在同一调用中跳过预检直接连写。`executeAdapter` 只接受 CommonJS 导出的工厂；不支持此加载方式的浏览器环境使用 direct-tool。当前工具能否提供 host 必须先验证，不能拿合成 host 冒充网页。

已经有可靠加载/落盘宿主时，也可以直接调用 `scripts/bounded_batch.js` 导出的异步函数：
```javascript
const { runBoundedBatch } = require(skillScripts + "/bounded_batch.js");
const state = persistedState || {runId: plan.runId, status: "prepared", results: {}};
const result = await runBoundedBatch(plan, preflight, adapter, state, Date.now, {
  maxOperations: 4,
  retryReadFailures: false
});
```
只在 host 支持这些加载与文件接口时使用；这段调用本身不创建浏览器。已用状态再次传入就是恢复，不需要创建空状态。v2 检查报告绑定当前计划；恢复可更新 probe、dispatchIds、已有授权，以及重新探测过的 locator/methodEvidence，但不能悄悄更改操作事实、记录、目标或原始预算。旧状态先人工/模型只读核对迁移，不能直接重放。

真实 adapter 必需：
- `source`：实际加载的 batch.js 原文，与预检原文直接比较，不做哈希。加载方法和执行函数必须来自该代码，不能拿别的文件原文凑一致。
- `identify({timeoutMs})`：从当前浏览器核对 target；不得固定返回计划目标。
- `inspect(op,{timeoutMs})`：返回真实 count/kind/value/anchorMatched；有歧义不取第一条。明确已结束的读取错误可抛带 `settled:true` 的错误；没有此证据则暂停。首次同类方法错误定点修一次，不能多轮追逐控件。
- `write(op,{timeoutMs})`：执行本次动作。返回 written；或 failed + noEffect:true + settled:true；或 partial + recordRef；或 unknown。部分修改不能标成 noEffect。每个内层调用共享这个截止时间，不重新获得完整 timeout。
- `checkpoint(state)`：await 可靠落盘；建议临时文件写入后原子替换。executeAdapter 已提供此实现，不接受只把状态推入内存数组作为生产落盘。状态的 workflow 会保存本轮计划副本用于恢复比对，应与计划放在同一私有目录；不能含证件值、凭证或原始工具错误。
- `confirmSettled(pendingCall)`：恢复时若上一远程调用尚未确认结束，返回 settled:true 和真实 evidence 才能继续读取网页。它检查宿主已返回的调用状态，不通过新的浏览器动作猜测。没有证据就保留 pending，不把用户说“继续”当作远程请求已结束。

可选 `completeRecord(op,{recordRef,timeoutMs})` 仅补此前已创建的那条空记录名称，绝不点击新增。恢复要求 inspect 确认同一稳定 recordRef、唯一空记录和 owned:true；序号或过期 AX id 不能充当稳定记录身份。补名称也先持久记录 pending，结果未知不能再次补。unknown 返回时只有调用已确定结束才带 settled:true；否则先经 confirmSettled 核实结束。未知新增却没有记录身份的证据则交接。

maxOperations 产生正常 paused 边界；下次带原状态继续。read-failed 仅当显式开启 retryReadFailures 才再试一次；这表示程序重试策略，不是再次要求用户授权。已完成项先回读，匹配则跳过，用户变更标 conflict。未知写入只读核对成功才继续，否则保持 pending。

## 分模块保存

计划可以增加：
```json
{"modules":[{"id":"education","operationIds":["F1","F2"]}]}
```
每个操作最多归属一个保存模块。模块边界按站点按钮作用范围确定，不能把整页保存伪装成某一条记录保存。在 direct-tool 中执行相同步骤；adapter 模式实现：
- `inspectModule(module,{timeoutMs})`：返回 canSave、missingRequired（仅字段标签）、revision（可选且不含敏感值的变化标识）。核对实际必填项和页面状态。
- `saveModule(module,{timeoutMs})`：只执行该模块保存，返回 saved / validation-failed（settled:true）/ unknown。仍需独立 verifyModule。
- `verifyModule(module,{timeoutMs,readOnly:true})`：不点保存、不刷新，返回 saved:true 和真实 evidence（工具编号、成功提示/只读卡片等），否则 saved:false。

只有 authorization.save === true 才保存。模块相关目标都核对通过后才进入保存；缺必填只阻塞本模块。已保存模块不会自动重存。明确 validation-failed 后，同一 revision 不重试；缺项实际变化并重新检查时才可再尝试。保存未知时 pendingSave 优先核对，未解决前不继续写入或其他保存。没有通用自动提交入口。

保存确认后，若需要且已授权，可在没有其他未保存草稿时重开一次并回读。不能为了验证一个模块而丢掉另一模块草稿。

## 时间、旧状态与证据边界

30 分钟从准备开始计算；普通操作 120 秒，保存 180 秒，外层额外容纳返回开销。当前 API 不支持的参数不能编造；read/count 和其他内层动作也要分配剩余时限。超时无法证明远程调用已取消。

v1 状态、旧 preflight 和历史 batch 只作证据，不执行它们的未知动作。迁移时保留原始时间和未决状态，按当前网页匹配已完成记录；预算耗尽先交接，用户明确续作后再记录新的预算依据。

执行循环约束通过它的调用，不会审计模型是否绕开工具，不会独立验证调用者填写的 probe/evidence，也不能使错误适配器自动正确。测试使用合成适配器；真实网站是否可用仍需要当前浏览器上的实际调用证据。

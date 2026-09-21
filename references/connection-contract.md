# 连接检查器

`scripts/connection_guard.py` 是连接前后的本地状态检查器，不是浏览器驱动。用已获准的文件/命令工具调用它，再用当前浏览器工具执行被允许的动作；不创建调试服务、不修改浏览器权限。状态保存在本轮目录 `connection-state.json`，不包含表单值或原始浏览器错误。

## 发现与失败处理

先查看当前真实工具清单及文档，调用它支持的发现接口，确认已有目标页。`browser_tabs` 只是可能的接口名，不要求宿主提供它，也不假设它能访问 In-app Browser。工具不可发现目标时停止报告，不创建新会话、不安装浏览器或连接服务。

将发现调用的 `callId/tool/settled/existing/target` 写入计划的 `execution.discovery`；`target` 包含 `browser/url` 以及工具实际提供的 `tabId` 或 `pageId`。提供会话标识时也保留。没有可靠身份就交接，不编造标识；不同接口的标识不能直接互换。已有可用句柄不需要人为制造一次连接尝试。

缺少浏览器依赖时明确报告实际报错中的所需版本、查到的已安装版本（未查到为未知）、文档支持的安装/切换方案及其是否会新建会话；不自行实施修复。创建新会话不能保证继承登录状态和草稿。版本错误与单个字段 DOM 定位失败分开处理。

CUA 默认关闭，由用户明确决定启用或拒绝，不主动请求开启。已明确启用时，可通过 CUA 的实际观察接口确认同一页面后更新 discovery/probe；它不证明 DOM 连接恢复。无可靠目标身份或存在未结束调用仍须停止。

## 顺序

1. 用当前浏览器工具公开的发现接口取得浏览器和目标标签页元数据。初始化一次；已有状态用 `show` 读取，不能换目录或重新初始化清零。首次不存在的状态只能在本轮确实尚未尝试连接时创建。
2. 选择当前文档允许且符合页面归属的路径，用发现接口返回的元数据 `reserve`。只有退出码 0 且 `dispatchAllowed: true` 才允许执行本次句柄获取。登记会先落盘 pending，再返回调用许可。
3. 浏览器工具调用使用输出的 `operationTimeoutMs: 120000`；外层时限至少容纳一次调用和返回开销（检查器输出 125000 ms 的建议最小值）。多个串行浏览器调用需要分别安排外层时间；不能把 125 秒当整个多步骤流程的时限。这里输出的是执行参数要求，不会自动改变浏览器工具或取消远程调用。
4. 取得句柄后轻量只读核对目标，不读取身份字段。确认返回页面匹配才登记 `verified`；后续复用句柄。失败按实际返回登记结果，不把所有错误归类为可重试。

示例（替换为当前工具实际观察到的浏览器、URL、ID；路径相对 skill）：

```text
python scripts/connection_guard.py --run <run> init --browser <browser-id> --url "https://example.test/form"
python scripts/connection_guard.py --run <run> reserve --route <actual-method> --action-kind attach-existing --tool <actual-tool> --discovery-call-id <returned-call-id> --browser <browser-id> --url "https://example.test/form" --tab-id <observed-tab-id>
```

此时用当前获准浏览器工具执行已登记的路径；下面两种 finish 只选符合实际结果的一种：

```text
python scripts/connection_guard.py --run <run> finish --attempt 1 --outcome verified --settled --probe-passed --tab-id <observed-tab-id> --browser <browser-id> --url "https://example.test/form"
python scripts/connection_guard.py --run <run> finish --attempt 1 --outcome unattached --settled
```

如用户的标签页明确提及还包含标题和 providerTabId，初始化、登记和成功核对时传入 `--title`、`--provider-tab-id`；检查器会将它们加入精确匹配。不同接口的 ID 不能直接互换，备用路径必须使用新清单提供的正确 ID。

只有明确结束的 `unsupported`（当前路径不支持）、`stale`（旧句柄失效）、`unattached`（调试器未附着）允许使用另一条路径登记第二次；第三次以及原样重复同一路径被拒绝。`occupied`、`interrupted`、`timeout`、`unknown`、`dependency-missing`（依赖缺失）、`discovery-unavailable`（发现不可用）停止本轮接管。最后两类不能当作自动换浏览器或 CUA 的许可。外层超时不代表底层已结束，不能据此填写 `--settled`。

若进程中断留下 pending，或状态读写失败，不继续接管。命令以互斥锁保护同一运行目录，并以临时文件替换提交状态；残留锁需要确认没有进程仍在操作后再排查，不能盲目删除。

## 能保证与不能保证的部分

- 能拒绝本运行目录内的第三次尝试、同路径重试、目标不匹配、pending 时重接，以及占用/未知状态之后的备用路径。
- 不能证明输入的浏览器清单、错误类别和 probePassed 真实；必须来自实际工具结果。
- 不能锁住其他 Codex 任务的浏览器会话；真正的标签页归属由浏览器工具管理。本地文件锁只用于状态文件，不是跨任务的标签页锁。
- 不能拦截模型直接调用工具、提高工具硬时限或中止远程请求。不要称为工具层强制隔离。
- 当前默认固定为两次连接尝试、单次 120 秒。若用户明确要求其他配置，须先修改并验证检查器；不要手改状态或另建 run 规避限制。

`bounded_batch.js` 继续管理填写阶段；连接成功只证明可以读取目标，不代表填写或保存成功。

连接状态版本现为 2，旧版状态不能直接重用；保留旧文件和尝试预算，先核对现场后迁移，不重新初始化绕过限制。检查器只接受 attach-existing，具体 route 是现场接口名，必须附发现调用和工具名称。

# 浏览器执行通道

本仓库现在提供两条互相独立的执行通道。它们解决的是浏览器控制层问题，不会替代真实页面勘察、独立规则核验或保存回读。

## A. `safeGoto`：同一 CDP 会话的安全导航

文件：

- `scripts/cdp_connection.js`：只连接明确提供的回环 CDP 发现地址，不启动浏览器。
- `scripts/safe_goto.js`：固定执行 `Page.enable → Runtime.disable → Page.navigate → 稳定加载 → Runtime.enable`。

使用方必须提供一个已经建立的 CDP session，session 至少有 `send(method, params)`、`on(event, listener)` 和 `off(event, listener)`。模块不会创建第二个 tab、重放超时导航或在 Runtime 关闭期间执行 `Runtime.evaluate`。

如果加载或重定向超时，错误会带 `settled: false`；调用方必须先重新读取当前页面，不能直接再次导航。Runtime 恢复失败也会停止流程。

## B. `via-extension`：Edge MV3 页面脚本通道

文件：`extension/edge-via-extension/`。

扩展的 service worker 通过 `chrome.scripting.executeScript` 的 `MAIN` world 执行白名单页面命令。它不声明调试器权限，也不接收任意 JavaScript；支持 `identify`、`snapshot`、`discover`、受限 `click`、`inspect`、`fill`、模块检查/保存回读和受限导航。`click` 只接受文字和唯一 locator 均匹配的“编辑/去补充/展开”控件；保存按钮必须有现场观察到的唯一 locator，文字疑似“提交/投递/申请”的控件会被拒绝。

本地桥接服务：

```powershell
node scripts/via_extension_bridge.js --port 43127
```

命令行会输出一次本地 endpoint 和 token。将两项填入扩展的 Options 页面；桥接服务只监听回环地址，并要求 Bearer token。桥接服务收到超时命令后不会取消或重发，客户端会返回 `unknown`，必须使用 requestId 查询并核对页面。

扩展和桥接服务支持有限并发：不同标签页可以同时处理，同一标签页按命令串行；桥接层按 tabId 保留在途锁。扩展重载后，已派发但没有回执的命令不会自动重放，应在确认旧页面动作不再继续后显式调用 `reconcileUnknown(requestId, reason)`，再进行只读核对。

扩展加载、配置和当前 Edge 标签切换仍属于真实浏览器动作；本仓库只提供文件和桥接协议，不能把“文件已生成”当成“扩展已安装或已执行”。

## 与网申执行器的关系

`createViaExtensionAdapter()` 返回符合 `bounded_batch.js` 的 `identify/inspect/write/inspectModule/saveModule/verifyModule` 适配器。真实运行前仍需要：

1. 在 Edge 中观察目标页并记录唯一 locator、记录边界、必填项和保存提示。
2. 用这些现场证据生成计划和 `before.json`，运行 `preflight.py`。
3. 通过 extension adapter 做短批次填写；未知写入先只读核对，不重放。
4. 由独立内容核验者检查后再按模块暂存；本通道没有正式提交命令。

当前 CUA 接口能够发出 `Runtime.disable`，但不提供底层 `Page.navigate`，因此不能把 CUA 的高层 `goto()` 拼在 `Runtime.disable` 后冒充 `safeGoto`。若当前 Edge 没有已启用的回环 CDP 端口，应使用 extension 通道，或由用户提供一个明确可连接的 CDP session。

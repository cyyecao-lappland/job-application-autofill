# Edge via-extension 通道

这是本地开发版 MV3 扩展。它通过 service worker 轮询本机桥接服务，再用 `scripting.executeScript` 的 `MAIN` world 在当前猎聘/BOSS 标签执行白名单命令。页面入口通过 `discover` 发现，`click` 只接受文字和唯一 CSS 定位均匹配的“编辑/去补充/展开”控件。

## 一次配置

1. 在本机启动桥接服务：`node scripts/via_extension_bridge.js --port 43127`，保存命令行输出的 endpoint 和 token。
2. 在 Edge 打开 `edge://extensions`，打开开发人员模式，选择“加载已解压的扩展”，目录选本文件夹。
3. 打开扩展 Options，填入 endpoint 和 token，保存。
4. 登录并打开目标简历页。桥接服务只会把命令交给当前活动的猎聘或 BOSS 标签。

加载扩展会改变 Edge 的本地扩展状态，必须由用户在浏览器中确认；本仓库不会静默安装。扩展没有正式提交命令；入口点击和保存按钮都必须经过唯一 locator 与文字安全检查。

## 并发与恢复

扩展采用有限并发：最多同时服务 6 个独立标签页；同一标签页始终一次只执行一个命令。桥接服务也会按 tabId 拦截同一标签页的并发派发，因此扩展后台重载不会让两个写入同时进入同一份草稿。

如果扩展在命令已经派发后被重载，桥接服务会把该命令保留为 `dispatched`，不会自动重放。确认页面上的旧动作已经无法继续后，调用桥接客户端的 `reconcileUnknown(requestId, reason)` 将它标记为 `unknown`；这一步只释放队列，不宣称写入成功，之后仍须先只读核对页面。

## 计划 locator

页面命令不接受任意脚本。每个自动字段必须携带现场观察到的唯一 `locator`，例如：

```json
{
  "label": "项目名称",
  "kind": "text",
  "before": "",
  "value": "来源中确认的项目名称",
  "locator": {
    "scopeSelector": "[data-record-id=\"observed-record\"]",
    "selector": "input[name=\"projectName\"]"
  }
}
```

示例中的选择器只是格式示例，不能直接用于猎聘页面；必须从当前页面重新观察并记录。字段写入后扩展会立即读回，冲突、歧义和读回不匹配不会自动重试。

# 新电脑部署网申专用 Edge

网申专用 Edge 使用已安装的 Microsoft Edge，加上独立用户目录和本机调试端口。GitHub 提供安装和启动脚本，浏览器程序由微软提供。

## 下载后双击安装

1. 在仓库首页点击 **Code → Download ZIP**，解压整个 ZIP。
2. 双击根目录的 **install-edge.cmd**。
3. 安装完成后，桌面会出现 **网申专用 Edge**。首次安装会启动浏览器；以后双击桌面入口即可。
4. 在这个浏览器里重新登录招聘网站，然后打开需要填写的申请页。

脚本检测已安装的 Edge；缺失时通过 Windows 的 winget 安装 Microsoft.Edge。缺少 winget 时会提示从微软官网下载。微软安装程序可能要求管理员确认；企业管理策略也可能限制调试。

不需要 Git、Python 或 Node 来创建这个浏览器环境。网申执行程序的依赖仍按仓库首页单独安装；若已经装好网申程序，只需完成以上步骤。

## 默认位置

| 项目 | 默认值 |
| --- | --- |
| 启动脚本和本机配置 | `%LOCALAPPDATA%\JobApplicationEdge` |
| 独立浏览器用户目录 | `%LOCALAPPDATA%\Edge-Automation-CDP` |
| 网申程序连接地址 | `http://127.0.0.1:9333` |

用户名和 Edge 安装位置自动检测，不依赖旧电脑的 `C:\Users\19242`。用户目录和登录状态保存在当前电脑本地，不随 GitHub 下载。不要上传整个用户目录、Cookie、密码或个人投递记录；跨电脑复制用户目录不保证登录可用。

已有同路径用户目录会沿用，重复安装不会清空它。若端口已被其他程序或另一份 Edge 用户目录占用，启动器会报错；不会抢占端口或结束浏览器进程。普通 Edge 与专用 Edge 可以同时使用。

已有可验证的专用 Edge 调试连接时，启动器直接检查并返回，不新增标签页。独立浏览器已启动但没有开启调试时，请自行关闭该专用浏览器窗口，再从桌面入口启动。

## 只安装、暂不启动

在解压目录打开 PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_job_edge.ps1 -NoStart
```

自定义用户目录或端口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_job_edge.ps1 -ProfileDirectory 'D:\files\job-application\edge-profile' -Port 9333
```

重复安装已有配置时，传入相同的目录和端口。改用其他端口后，需要同步调整网申 `config.local.json` 的 `cdp_endpoint`。普通 Edge 的默认 User Data 及其子目录不能用作专用调试目录。

## 连接检查

浏览器启动后，在 PowerShell 执行：

```powershell
Invoke-RestMethod http://127.0.0.1:9333/json/version
```

返回 Edge 的版本信息代表调试服务可访问，不代表已经登录或提交了申请。已安装网申程序的用户，还可在仓库目录运行：

```powershell
node scripts/inspect_edge_cdp.mjs --endpoint http://127.0.0.1:9333
```

多个标签页时程序要求明确选择目标页，沿用现有的页面身份检查。

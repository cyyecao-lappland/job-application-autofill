# 本机配置与迁移

skill 根目录的 `config.local.json` 是安装位置的入口。公开仓库带 config.example.json；本机配置由安装者创建并保持独立。新示例的 workspace_root 和 project_directory 都为 .，执行代码就在仓库根目录。原先指向独立迁移目录的配置仍可使用，不自动覆盖。

| 字段 | 相对路径的基准 |
| --- | --- |
| `workspace_root` | 配置文件所在目录；保持原目录结构时只需修改这一项 |
| `project_directory` | `workspace_root` |
| `profile_file` | `workspace_root` |
| `python_executable` | `project_directory`；新机器先建立 `.venv` 并安装项目 |
| `cdp_endpoint` | 已运行 Edge 的本机 HTTP 调试地址；不会自动启动浏览器 |

路径支持绝对路径、环境变量和 `~`。不用将新机器设为原来的 Windows 用户名。程序及运行目录继续放在 OneDrive 外。

## 读取并交给现有执行器

执行 skill 自带的 `scripts/resolve_config.py`，默认读取同一 skill 根目录的 `config.local.json`；也可通过 `--config` 指定文件。这是只读检查：解析路径、检查必要文件和既有程序配置，输出 JSON，不读取资料正文、不连接网站、不建立运行库。`ready` 仅证明本机必要路径存在，不证明依赖已安装、模型可用、浏览器已连接或申请已保存。

原生宿主仍使用解析结果中的 `native_entry`，显式传入 `--project`、`--profile`、`--python`、`--endpoint`。新运行的 `--run-dir` 使用 `prior_root` 下的目录，`--prior-root` 使用原历史日志的真实根目录；存在其他历史根目录时按原任务记录选择，不能为了迁移改成空目录。继续旧任务时沿用原任务身份和状态，不因配置变化重放动作。CLI 的具体起始/恢复参数仍按项目 README 查询；配置文件不代表填写、保存或提交授权。

模型与正式映射仍由程序的 `private/local-config.json` 管理，不在安装配置里重复。`knowledge_file` 原生支持相对项目目录的路径，迁移包优先保留 `private/form-knowledge.json` 这一形式；如现有配置使用旧绝对路径，改为新位置。资料始终显式传给原生宿主及接受 `--profile` 的辅助命令，不依赖程序旧配置中的绝对资料路径。

## 迁移范围

- skill 与完整执行源码现可从同一仓库下载；个人资料和附件、正式映射与控件方法、程序私有配置、投递台账及原 journal 仍单独迁移。
- `.venv`、`node_modules` 在新机器重建；招聘网站重新登录。保留日志不等于恢复了旧页面和未保存草稿。
- 安装配置只管理安装位置。资料 JSON 中附件的旧绝对路径、程序旧 `migration_paths`、任务内已存的绝对路径需按迁移情况另行核对；不能据此承诺所有状态只改一个字段即可恢复。不要批量替换原 journal 或复制任务后直接执行。
- 配置文件缺失或路径无效时报告具体问题，不回退旧电脑路径或把公开示例资料当本人资料。

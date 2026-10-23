# Codex Account Manager

切换 Codex 官方登录和 API 网关，在同一个会话中接着开发；也可以为多个官方账号保留独立的 CLI 环境。需要 Python 3.11+，仅依赖标准库。

[English](README.md) · [Agent 开发说明](AGENTS.md) · [MIT 许可证](LICENSE)

| 命令 | 用途 | 系统 |
| --- | --- | --- |
| `cs` | 官方登录、Responses API 网关之间交接同一会话 | Linux |
| `cx` | 在隔离的 CLI 目录中手动切换官方账号 | Linux、macOS、Windows |

## 安装并使用 cs

克隆或下载仓库后，在仓库目录运行：

```bash
./cs install
source ~/.bashrc
cs add gateway
cs use gateway
cs resume
```

添加网关时选择 proxy，输入 API 根地址、API key（隐藏输入）和模型名。网关必须支持 Responses API。账号保存在仓库外的 `~/.config/codex-switch`，采用仅本人可读写的权限。

安装器在 `~/.local/bin` 中建立 `cs` 和仅使用官方 provider 的 `codex` 入口，并保留原来的 Codex/HUD 启动器。`codex resume` 使用原生官方选择器，`cs resume` 使用本工具的跨 provider 选择器。已有 Git 工作区安装会直接使用更新后的代码，无需重新安装。

## 轮流接着开发

```bash
codex resume                     # 官方账号继续聊天
cs resume                        # 选择同一会话，请求旧端释放并接管
# 在 cs 中输入 /exit，释放写入权
codex resume <同一会话的ID>        # 使用官方账号，继续读取代理端新增的历史
```

两边都可以使用完整 session ID 或 `cs sessions` 列出的编号。`resume --last` 跨 provider 查找当前目录最新会话；加 `--all` 可以搜索其他目录。原生选择器可能过滤掉最后由代理保存的会话，切回官方时使用相同 ID，或用 `cs native resume` 打开明确标注为官方的跨 provider 选择器。编号绑定到当前 home 和数据库，切换存储目录后重新运行 `cs sessions`。

启动提示与终端标题会标明 `CODEX OFFICIAL`（官方）或 `CS PROXY / <账号>`（代理），启动提示和工具选择器还会显示实际网关域名。网关配置中的协议名称可能因兼容需要保留 OpenAI；判断凭据来源时看明确的启动身份标识。`cs who` 分别显示当前进程继承的启动身份、下一次 `cs` 启动的选择；`cs use` 不会把普通 `codex` 切换到代理账号。

受管理终端使用独立的本地写入进程。接管时先请求旧终端正常退出，确认锁释放后再启动新写入者。session ID 和已保存历史保持连续，`/exit` 释放写入权。

共享 daemon（包括连接它的旧 CLI 或 App）持锁时，通过服务端 API 临时归档并立即恢复目标会话及其子会话，关闭旧写入者后接管。原归档状态保存在恢复日志中；中途失败后重试同一命令，会先进行恢复。旧窗口可能短暂收到归档通知，其他无关会话和 daemon 继续运行。

不连接 daemon、也没有通过新入口启动的旧独立终端，需要先 `/exit` 一次。接管会中断目标会话及其子会话正在运行的任务；未发送草稿不属于已保存历史。如果交互弹窗或自定义快捷键阻止退出，需要在旧端手动 `/exit`。暂不接管远程 daemon。

## 常用命令

| 命令 | 说明 |
| --- | --- |
| `cs` | 使用当前选中的账号开始聊天 |
| `cs add <名称>` | 交互添加官方或网关账号 |
| `cs add --force <名称>` | 更新账号，取消输入不会覆盖旧内容 |
| `cs ls`、`cs who`、`cs use <名称>` | 列出、查看、选择账号 |
| `cs rm <名称>` | 删除已保存账号 |
| `cs sessions [数量] [--all]` | 列出会话；`--all` 也显示归档和子会话 |
| `cs resume [id]`、`cs continue` | 选择会话，或继续当前目录最新会话 |
| `cs <名称> [参数...]` | 临时使用指定账号 |
| `cs exec resume <id> "任务"` | 非交互接续，同样检查写入权 |
| `cs native [参数...]` | 明确使用官方登录 |

`cs run <名称> exec resume ...` 和 `codex exec resume ...` 也支持交接。提示词以 `-` 开头时使用 `--` 分隔。选择器支持中文筛选、方向键、PageUp/PageDown，Esc 或 Ctrl+C 取消。

会话数据库依次采用 `config.toml` 中的 `sqlite_home`、`CODEX_SQLITE_HOME`、`CODEX_HOME`。恢复时应在这些位置配置数据库目录，不使用临时 `-c sqlite_home` 覆盖，以便列表与实际写入者保持一致。

## 添加 Poe

```bash
cs add poe --preset poe
cs use poe
cs resume
```

按提示隐藏输入 key，并指定 Poe bot/model 名称。预设采用 `https://api.poe.com/v1`、Responses 协议、独立 API key 和 HTTP 流式请求。[Poe 官方文档](https://creator.poe.com/api-reference/createResponse)支持流式 Responses 和 function tools；具体 bot 的工具、参数及上下文限制仍需实测，可用 `--model` 或 `-c model_context_window=...` 按实际模型调整。

## 用 cx 隔离多个官方账号

Linux/macOS 在仓库目录运行 `./cx install`，按提示把安装目录加入 PATH。Windows 运行 `install.cmd` 后打开新终端，或直接使用仓库中的 `cx.cmd`。

```bash
cx init
cx login personal
cx login work
cx ls
cx personal resume --last
cx work resume --last
```

默认使用官方设备码登录，显示返回的邮箱并要求确认后才保存；`--browser` 可选择浏览器登录，`--with-api-key` 通过 stdin 读取 API key。不要把凭据贴到聊天或 issue 中。

所有 `cx` 账号共享一个隔离 CLI 目录。初始化仅从原 home 复制一次配置、说明、skills/rules 和会话历史；SQLite 使用一致性备份，并重定向复制后的会话路径。不导入 App 登录、daemon 状态、浏览器会话或插件凭据，之后也不会与 App 实时同步。

`cx add <名称>` 保存当前隔离登录；`cx use <名称>` 切换账号；`cx use --off` 恢复之前的隔离登录；`cx who` 查看当前账号；`cx test` 只检查本地登录状态；`cx rm <名称>` 删除未选中的账号。切换前退出其他 `cx` 会话即可。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CS_DIR` | `~/.config/codex-switch` | 网关账号及交接状态 |
| `CODEX_CLI_PATH` | 安装器记录的原启动器 | 下游 Codex 可执行文件 |
| `CA_CODEX_DIR` | `~/.config/codex-accts` | 隔离账号存储 |
| `CX_HOME` | 同级 `codex-cli` 目录 | 隔离 CLI 状态 |
| `CX_APP_HOME` | 继承的 `CODEX_HOME` 或 `~/.codex` | 初始化时只读的导入源 |
| `CX_BIN_DIR` | `~/.local/bin`；Windows 本地应用目录 | `cx` 安装位置 |

隔离账号目录、CLI home 和导入源必须分开且互不嵌套。凭据应保存在 Git、共享文件系统和云同步目录之外。

## 验证

Linux 使用 Python 3.11+：

```bash
python3 -m unittest discover -s tests -v
```

macOS/Windows 使用 `python -m unittest discover -s tests -p test_codex_accounts.py -v`。CI 在三个系统验证隔离账号功能，在 Linux 验证共享会话功能。

设置 `CS_TEST_CODEX_BIN=/path/to/codex` 可运行真实 CLI/HUD 测试。测试使用临时 home、合成凭据和本地模拟 Responses 服务，覆盖双向接续、工具执行和结果回传、daemon 交接及异常恢复。它们不使用现有账号或付费模型，也不能代替真实 Poe/网关账号与模型的联调。

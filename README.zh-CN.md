<div align="center">

# ca

**在同一个工具里管理你自己的多个 Claude 或 Codex 账号，支持终端和 VS Code。**

[![ci](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml/badge.svg)](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![platform](https://img.shields.io/badge/platform-linux%20%C2%B7%20macOS%20%C2%B7%20windows-black)](#支持的系统)

[快速开始](#快速开始) · [Codex](#codex-多账号) · [命令](#命令) · [VS Code](#vs-code-扩展) · [故障排查](#故障排查) · [给 agent 看的](AGENTS.md) · [English](README.md)

</div>

---

Claude 继续使用原来的 `ca ...` 命令；Codex 使用独立的 **`cx ...`** 命令。原来的 `ca codex ...` 写法也可以继续使用。两套账号分别保存和选择，只用其中一种也可以。下面的 Claude 用法保持有效，Codex 请看[Codex 多账号](#codex-多账号)。

## Codex 多账号

`cx` 现在使用**独立的 CLI 目录**。Codex App、IDE 扩展和普通 `codex` 命令继续使用原来的登录；`cx` 只给自己启动的 CLI 设置 `CODEX_HOME`，不改用户环境变量，也不替换 App 的 `auth.json`。

需要 **Codex CLI** 和 **Python 3.11+**，只依赖 Python 标准库。已有 Git 安装立即使用当前代码；下载文件安装时保留 `cx`、`cx.cmd` 和 `codex_accounts.py`，运行 `install.cmd`。

```powershell
cx init                         # 可选：现在初始化独立目录，首次使用也会自动初始化
cx login work                   # 默认设备码登录，核对邮箱，输入 y 保存
cx login personal               # 再保存另一个账号，建议在无痕窗口授权
cx ls
cx work                         # 使用 work 启动 CLI 新聊天
cx personal resume --last        # 使用 personal 接着本项目最近的聊天
# cx personal resume            # 打开历史选择器
```

初始化会把原有配置、全局说明、skills、rules、profile 配置和本地会话记录复制到 CLI 目录。会话数据库通过 SQLite 一致性备份复制，其中的会话文件路径会改指向 CLI 副本。不会导入 App 的登录凭据、后台进程状态、沙箱密钥、浏览器状态和插件登录。交互式 CLI 使用 `--no-daemon`，避免连接 App 的共享后台。

所有 `cx` 账号使用同一个独立 CLI 目录，因此切换 CLI 账号后仍保留配置和本地上下文。要继续旧聊天，请使用 `resume`；单独运行 `cx work` 是新聊天。来自 App 的内容是**初始化时的一次性快照**，不是双向同步：之后 App 的新消息不会自动出现在 CLI，CLI 的新消息也不会自动回写 App。不同客户端版本、正在写入的会话或账号专属服务可能影响个别导入聊天的恢复；也不保证跨账号命中缓存或节省额度。

| 命令 | 功能 |
| --- | --- |
| `cx init` | 初始化独立 CLI 目录并显示两个目录位置；首次使用自动执行 |
| `cx login <名字>` | 默认官方设备码登录，显示实际返回的邮箱，输入 y/yes 才保存 |
| `cx login <名字> --browser` | 显式使用浏览器回调登录，仍需要邮箱确认 |
| `cx login <名字> --device-auth` | 显式指定设备码登录，与默认相同 |
| `cx login <名字> --with-api-key` | 从标准输入读取 API key；这种登录无法显示邮箱 |
| `cx add <名字>` | 保存当前独立 CLI 的登录，不会自动保存 App 主账号；`save` 是别名 |
| `cx ls` / `cx who` | 列出账号 / 查看独立 CLI 当前账号 |
| `cx use <名字>` | 只选择之后 `cx` 启动时使用的 CLI 账号，不影响 App 和普通 codex |
| `cx <名字> [参数…]` | 选择账号后启动本地 CLI，转交 Codex 参数 |
| `cx use --off` | 恢复第一次独立 CLI 切换前的登录；最初无登录则清除 CLI 凭据，不改 App |
| `cx rm <名字>` | 删除保存的账号；管理中的当前账号需要先切走或恢复 |
| `cx test` | 在独立目录中检查本地 login status，不调用模型，也不验证服务端是否仍接受凭据 |

切换 CLI 账号前退出旧 `cx` 会话即可，App 可以继续开着。`cx` 不转交启动 App、连接共享后台/远程服务、覆盖凭据/数据库/日志目录等命令，避免脱离隔离模式。

设备码链接请在无痕窗口打开并登录目标账号，普通窗口可以继续保持主账号。对应 ChatGPT 安全设置或工作区权限可能需要先启用设备码登录。登录后显示邮箱并询问 `Save this login as 'work'? [y/N]`；取消、读不到邮箱或输入结束时，不会新增或覆盖账号，临时文件会清理。`--force` 允许覆盖同名账号，但不跳过邮箱确认。API key 登录沿用标准输入，不做邮箱确认。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CA_CODEX_DIR` | `~/.config/codex-accts` | 保存账号副本和 CLI 恢复备份 |
| `CX_HOME` | `CA_CODEX_DIR` 旁边的 `codex-cli` 目录 | 独立 CLI 的配置、凭据、会话、数据库和日志 |
| `CX_APP_HOME` | 继承的 CODEX_HOME；没有则 ~/.codex | 初始化时只读的 App 来源目录 |

这三个目录必须分开、互不嵌套；保存凭据的目录必须位于 Git 仓库外。只在 CLI 子进程内设置独立的 CODEX_HOME、CODEX_SQLITE_HOME，并通过命令参数指定文件凭据存储、独立数据库和日志目录，保留复制来的 config.toml 内容。组织强制策略仍然适用。依据：[官方状态目录与环境变量说明](https://learn.chatgpt.com/docs/config-file/environment-variables)。

已有保存账号可以继续使用。旧版共用登录的 `switch-state.json` 备份会保留，不会被新版本自动恢复到 App。如果之前已用旧版本把 App 切成副账号，需要在 App 中确认或重新登录主账号；隔离版本保留 App 当前状态。Windows 使用仅当前用户的 ACL，Unix 使用目录 700、文件 600 权限；凭据副本是明文秘密，不要分享、提交或放到同步目录。

不支持自动轮换、Codex 额度查询、通过 `ca push` 传输 Codex 账号或多个 cx 账号同时运行。启动账号前请清除可能覆盖登录的 API/access-token 环境变量。自定义 provider 和外部插件可能需要单独配置或授权。

以下为原有 Claude 功能说明。

```console
$ ca usage
✳ work
  5 小时  █░░░░░░░░░   2%   Sep 20 05:20 重置
  本周    ██████████  97%   Sep 23 07:00 重置
✳ research
  5 小时  ██░░░░░░░░  18%   Sep 20 06:10 重置
  本周    █░░░░░░░░░   2%   Sep 23 21:00 重置

$ ca research --continue      # work 的周限额用完了？换个账号，接着刚才的会话
```

会话历史存在本机 `~/.claude/`，不属于账号，所以换账号后 `--continue`、`--resume` 照样接得上。

- **只用官方凭据**：token 由 `claude setup-token` 生成。不经代理、不经网关，你和 Anthropic 之间没有第三方。
- **只对一条命令生效**：`ca <名>` 只为这次调用注入 token；直接运行 `claude` 仍是 `/login` 登录的账号。
- **token 不乱跑**：存在 `~/.config/claude-accts`，权限 600，不在仓库里；`CA_DIR` 指到 git 仓库里会被直接拒绝。

> [!IMPORTANT]
> 只适合切换**你本人名下**的账号，而且是手动切换。共享账号、或做成自动轮换的号池，违反 Anthropic 的使用条款。

## 快速开始

```bash
# 1. 安装（每台机器）
git clone https://github.com/Hliudi/Claude-Account-Management.git ~/.claude-accts
~/.claude-accts/ca install            # Windows：双击 install.cmd

# 2. 每个账号生成一个 token，在任意一台有浏览器的机器上做
claude setup-token                    # 用账号 A 登录，复制 token
claude setup-token                    # 换账号 B 再来一次

# 3. 存起来（输入不回显）并验证
ca add work
ca add research
ca test
```

`ca install` 会把 `ca` 加进 `PATH`、建好 token 目录，在交互终端里还会直接带你走完第 3 步。token 有效期约一年。

已经在别的机器配好了？直接通过 ssh 复制过去：`ca push user@host-a host-b`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `ca <名> [参数…]` | 用该账号启动 Claude Code，参数原样传给 `claude` |
| `ca <名> --continue` | 同上，但会先显示将要接续的是哪个会话（`-c` 同样有效） |
| `ca install` | 安装命令，并交互式添加账号 |
| `ca setup-token` | 生成 token；只装了 VS Code 扩展的机器也能用 |
| `ca add <名>` | 保存一个 token（隐藏输入，也可从管道读） |
| `ca ls` | 列出账号、打码的 token、选定的账号 |
| `ca who` | 当前会话在花谁的额度，会话内部也能查 |
| `ca usage [名…]` | 每个账号的 5 小时和每周额度 |
| `ca test [名…]` | 每个账号真实调用一次，验证 token |
| `ca rm <名>` | 删掉一个账号 |
| `ca use [名\|--off]` | 选定 VS Code 扩展用哪个账号 |
| `ca vscode [--apply]` | 配置 VS Code 用的 wrapper |
| `ca push <主机>…` | 通过 ssh 把脚本和 token 部署到其他机器 |
| `ca update` | `git pull` 这份 clone，从它安装的机器跟着更新 |

账号名自己定：字母、数字、`_`、`-`，不与子命令重名即可。名字后面就是 Claude Code 自己的命令行，短选项照用：`ca work -c`、`ca work -r`、`ca work --model opus`。

## 支持的系统

| 系统 | 用哪个 | 安装 |
| --- | --- | --- |
| Linux、WSL、服务器 | `ca`（bash） | `./ca install` |
| macOS | `ca`（bash 3.2 起） | `./ca install` |
| Windows | `ca-win.ps1`（PowerShell 5.1 / 7） | 双击 `install.cmd` |
| Windows + Git Bash | `ca`（bash） | `./ca install` |

两个版本共用同一个 token 目录，一台 Windows 上随便用哪个都行。

**只装了 VS Code 扩展、没装命令行？** 照样能用。扩展自带一个 `claude` 二进制，`PATH` 里找不到命令行时 `ca` 会自动回退到它，所以 `ca setup-token`、`ca test`、`ca <名>` 都能跑。而扩展内部的账号切换本来就不需要命令行。

## VS Code 扩展

扩展自己启动 Claude，所以它跟的是「**选定**的账号」，而不是你在终端里设的环境变量。

**Linux · macOS · Remote-SSH** —— 配置一次，之后随便切：

```bash
ca vscode --apply     # 生成 wrapper，并把 claudeCode.claudeProcessWrapper 指向它
                      # 然后执行一次 "Developer: Reload Window"
ca use research       # 新开和恢复的会话用 research；正在跑的会话不变
ca use --off          # 改回 /login 的账号
```

Remote-SSH 要在远程那台机器上运行，它写的是 `~/.vscode-server/data/Machine/settings.json`。设置文件里有注释时不会被改动，只打印出要粘贴的那一行。

**Windows** —— 扩展不能用 `.cmd` 当 wrapper，所以 `ca use <名>` 改为设置用户级环境变量 `CLAUDE_CODE_OAUTH_TOKEN`。完全退出 VS Code 再打开即可，新开的终端也会用这个账号。

## 故障排查

| 现象 | 原因和处理 |
| --- | --- |
| 刚装完就 `ca: command not found` | PATH 写进了 shell 配置文件，新开一个终端，或 `source ~/.zshrc` |
| `ca test` 报 `401 OAuth access token is invalid` | token 过期或粘错了：重新 `claude setup-token`，再 `ca add <名>` |
| VS Code 还在用旧账号 | Linux/macOS 需要新开会话；Windows 需要完全退出重开。`ca ls` 里 `*` 标的就是选定的账号 |
| `ca vscode --apply` 跳过了我的设置文件 | 那个文件有注释，把打印出来的那一行自己粘进 `Preferences: Open User Settings (JSON)` |
| 提示拒绝在这里存 token | `CA_DIR` 指到了 git 仓库里，用默认值或换个位置 |
| 切换后第一轮又慢又费额度 | prompt cache 按账号隔离，上下文要重读一次。挑任务之间切，别在一件事做到一半时切 |

## 参考

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `CA_DIR` | `~/.config/claude-accts` | token 和选定账号存在哪 |
| `CA_BIN_DIR` | `~/.local/bin`（Windows：`%LOCALAPPDATA%\claude-accts\bin`） | 命令装到哪 |
| `CA_LANG` | 跟随系统 | 设成 `zh` 或 `en` 强制输出语言 |
| `CA_YES` | 未设置 | 设成任意值即跳过 `--continue` 的确认 |
| `NO_COLOR` | 未设置 | 设成任意值即关闭彩色输出 |

还需要知道的：

- **现在用的是哪个账号？** `ca who`。在命令行的 Claude Code 会话里直接输入 `!ca who`，`!` 开头的命令会在当前会话里执行，不用另开终端。它以 token 为准；如果环境里只剩下 `CA_ACCOUNT`，会显示成「启动时用的是 …」，因为继承来的变量可能比设置它的那个会话活得更久。终端标签页也会改名。
- **`--continue` 会先问一下。** 它会打印会话标题、最后活动时间和最后一条消息，等你回车再继续。`CA_YES=1` 可跳过。
- **切换后 `/usage` 看不到额度条。** `setup-token` 的凭据缺 `user:profile` 权限，那个界面读不到数据，只能退回本地花费统计。改用 `ca usage`：它从 API 响应头里的限额字段读，任何凭据都有效，代价是每个账号一次 Haiku 调用（一个 token）。
- `--continue` 只能接**本机**的会话，历史不跨机器。
- `setup-token` 的凭据可能不带 claude.ai 连接器权限，缺功能就在那台机器上 `/login`。

## 许可证

MIT

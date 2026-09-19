<div align="center">

# ca

**在终端和 VS Code 里切换你自己的多个 Claude 订阅账号，每台机器都能用。**

[![ci](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml/badge.svg)](https://github.com/Hliudi/Claude-Account-Management/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-black)](LICENSE)
[![platform](https://img.shields.io/badge/platform-linux%20%C2%B7%20macOS%20%C2%B7%20windows-black)](#支持的系统)

[快速开始](#快速开始) · [命令](#命令) · [VS Code](#vs-code-扩展) · [故障排查](#故障排查) · [给 agent 看的](AGENTS.md) · [English](README.md)

</div>

---

```console
$ ca ls
  work           …k7Fq2A  2026-09-20
* research       …p2Xm9A  2026-09-20

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
| `ca <名> --continue` | 同上，但会先显示将要接续的是哪个会话 |
| `ca install` | 安装命令，并交互式添加账号 |
| `ca add <名>` | 保存一个 token（隐藏输入，也可从管道读） |
| `ca ls` | 列出账号、打码的 token、选定的账号 |
| `ca who` | 当前会话在花谁的额度，会话内部也能查 |
| `ca test [名…]` | 每个账号真实调用一次，验证 token |
| `ca rm <名>` | 删掉一个账号 |
| `ca use [名\|--off]` | 选定 VS Code 扩展用哪个账号 |
| `ca vscode [--apply]` | 配置 VS Code 用的 wrapper |
| `ca push <主机>…` | 通过 ssh 把脚本和 token 部署到其他机器 |
| `ca update` | `git pull` 这份 clone，从它安装的机器跟着更新 |

账号名自己定：字母、数字、`_`、`-`，不与子命令重名即可。

## 支持的系统

| 系统 | 用哪个 | 安装 |
| --- | --- | --- |
| Linux、WSL、服务器 | `ca`（bash） | `./ca install` |
| macOS | `ca`（bash 3.2 起） | `./ca install` |
| Windows | `ca-win.ps1`（PowerShell 5.1 / 7） | 双击 `install.cmd` |
| Windows + Git Bash | `ca`（bash） | `./ca install` |

两个版本共用同一个 token 目录，一台 Windows 上随便用哪个都行。

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

- **现在用的是哪个账号？** `ca who`。在命令行的 Claude Code 会话里直接输入 `!ca who`，`!` 开头的命令会在当前会话里执行，不用另开终端；`ca` 已经把 `CA_ACCOUNT` 传了进去。终端标签页也会改名。
- **`--continue` 会先问一下。** 它会打印会话标题、最后活动时间和最后一条消息，等你回车再继续。`CA_YES=1` 可跳过。
- **切换后 `/usage` 看不到额度条。** 这是正常的：`setup-token` 的凭据不带订阅信息，客户端只能退回到本地花费统计。限额本身仍然按账号在服务器端生效，要看数字就登录 claude.ai 用那个账号查。
- `--continue` 只能接**本机**的会话，历史不跨机器。
- `setup-token` 的凭据可能不带 claude.ai 连接器权限，缺功能就在那台机器上 `/login`。

## 许可证

MIT

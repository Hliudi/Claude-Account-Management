# ca

在终端和 VS Code 扩展里切换你自己的多个 Claude 订阅账号，每台机器都能用。

[English](README.md)

```console
$ ca ls
  work           …k7Fq2A  2026-09-20
* research       …p2Xm9A  2026-09-20

$ ca research --continue      # work 的周限额用完了？换个账号，接着刚才的会话
```

会话历史存在本机 `~/.claude/`，不属于账号，所以 `--continue`、`--resume` 换账号照样能接上。

- 用官方的 `claude setup-token` 凭据，不经代理、不经网关，你和 Anthropic 之间没有第三方。
- `ca <名>` 只为这一条命令注入 token。直接运行 `claude` 仍然是 `/login` 登录的账号。
- token 存在 `~/.config/claude-accts`，权限 600，不在仓库里；放进 git 仓库会被直接拒绝。

> 只适合切换**你本人名下**的账号。共享账号、或者做成自动轮换的号池，违反 Anthropic 的使用条款。

## 安装

```bash
git clone https://github.com/Hliudi/Claude-Account-Management.git ~/.claude-accts
~/.claude-accts/ca install          # Windows：双击 install.cmd
```

安装程序把 `ca` 加进 `PATH`，然后逐个询问账号的 token，输入时不显示。token 用 `claude setup-token` 生成，每个账号做一次，在任意一台有浏览器的机器上都行，有效期约一年。

`ca update` 拉取最新版本。从 clone 目录安装的机器，运行的就是这份 clone。

| 系统 | 用哪个 | 安装 |
| --- | --- | --- |
| Linux、WSL、服务器 | `ca`（bash） | `./ca install` |
| macOS | `ca`（bash 3.2 起） | `./ca install` |
| Windows | `ca-win.ps1`（PowerShell 5.1 / 7） | 双击 `install.cmd` |
| Windows + Git Bash | `ca`（bash） | `./ca install` |

两个版本共用同一个 token 目录，所以一台 Windows 上随便用哪个都行。

## 使用

```bash
ca work                  # 用 work 启动 Claude Code
ca research --continue   # 用 research 接着上一个会话
ca ls                    # 列出账号、打码的 token、选定的账号
ca test                  # 每个账号真实调用一次，确认 token 有效
ca add <名>              # 再加一个账号
ca rm <名>               # 删掉一个
ca push host-a host-b    # 通过 ssh 把脚本和 token 部署到其他机器
```

账号名自己定，字母、数字、`_`、`-` 都行，不与子命令重名即可。名字后面的参数原样传给 `claude`。

## VS Code 扩展

扩展自己启动 Claude，所以它读的是「选定的账号」，而不是你在终端里设的环境变量。

**Linux、macOS、Remote-SSH** —— 设置一次，之后随便切：

```bash
ca vscode --apply        # 生成 wrapper，并把 claudeCode.claudeProcessWrapper 指向它
                         # 然后执行一次 "Developer: Reload Window"
ca use research          # 新开的会话（以及恢复的会话）用 research；正在跑的会话不变
ca use --off             # 改回 /login 的账号
```

Remote-SSH 要在远程那台机器上运行，它写的是 `~/.vscode-server/data/Machine/settings.json`。设置文件里有注释时不会被改动，只打印出要粘贴的那一行。

**Windows** —— 扩展不能用 `.cmd` 当 wrapper，所以 `ca use <名>` 改为设置用户级环境变量 `CLAUDE_CODE_OAUTH_TOKEN`。完全退出 VS Code 再打开即可，新开的终端也会用这个账号。

## 一些细节

- 用量和限额由服务器按账号计算，所以 `/usage` 显示的是切换后账号的数字。而 `claude auth status` 显示的邮箱仍来自本机 `/login` 的凭据。
- 切换后的第一轮会重读整段上下文（prompt cache 按账号隔离）。会话很长时，挑一个告一段落的时机再切。
- `--continue` 只能接**本机**的会话，历史不会跨机器。
- `setup-token` 的凭据可能不带 claude.ai 连接器权限。缺功能就在那台机器上 `/login`。
- 输出遵循 `NO_COLOR`；语言跟随系统，也可以用 `CA_LANG=zh` / `CA_LANG=en` 强制。
- `CA_DIR`、`CA_BIN_DIR` 可以改 token 目录和安装位置。

## 许可证

MIT

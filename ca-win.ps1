#
#   ca (Windows) — switch between your own Claude subscription accounts.
#
#   Docs: https://github.com/Hliudi/Claude-Account-Management
#   Works on Windows PowerShell 5.1 and PowerShell 7. Install: double-click install.cmd.
#   Tokens live in %USERPROFILE%\.config\claude-accts and are readable by you only.
#
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

$OnWindows = ($PSVersionTable.PSEdition -eq 'Desktop') -or $IsWindows
$CaDir  = if ($env:CA_DIR) { $env:CA_DIR } else { Join-Path (Join-Path $HOME '.config') 'claude-accts' }
$BinDir = if ($env:CA_BIN_DIR) { $env:CA_BIN_DIR }
          elseif ($OnWindows) { Join-Path (Join-Path $env:LOCALAPPDATA 'claude-accts') 'bin' }
          else { Join-Path (Join-Path $HOME '.local') 'bin' }
$Self     = $PSCommandPath
$SrcDir   = Split-Path -Parent $Self
$Reserved = @('install','add','ls','list','test','rm','update','push','use','vscode','who','whoami','usage','help')

# --- look & feel ---------------------------------------------------------------------------------
$Color = (-not $env:NO_COLOR) -and $Host.UI.SupportsVirtualTerminal -ne $false
$e = [char]27
$A = if ($Color) { "$e[38;5;209m" } else { '' }   # Claude coral
$D = if ($Color) { "$e[2m" }  else { '' }
$B = if ($Color) { "$e[1m" }  else { '' }
$G = if ($Color) { "$e[32m" } else { '' }
$R = if ($Color) { "$e[31m" } else { '' }
$X = if ($Color) { "$e[0m" }  else { '' }

function Say([string]$m)  { Write-Output $m }
function Note([string]$m) { Write-Output "$D$m$X" }
function Head([string]$m) { Write-Output "$A✳$X $B$m$X" }
function Ok([string]$m)   { Write-Output "$G✓$X $m" }
function Die([string]$m)  { [Console]::Error.WriteLine("$R✗$X $m"); exit 1 }

# --- messages (English by default, Chinese when the locale asks for it) ---------------------------
$Zh = if ($env:CA_LANG) { $env:CA_LANG -like 'zh*' }
      else { (Get-Culture).Name -like 'zh*' }
$Msg = @{
  installed    = @('Installed {0}',                 '已安装 {0}')
  via_repo     = @('  ↳ runs {0} — update with `ca update`', '  ↳ 实际运行 {0}，更新用 `ca update`')
  token_dir    = @('Tokens  {0}',                   'token 目录  {0}')
  path_hint    = @('Open a new terminal to get the `ca` command.', '新开一个终端后 ca 命令生效。')
  no_claude    = @('Claude Code is not installed here yet — install it before running `ca <name>`.',
                   '这台机器还没装 Claude Code，装好后才能用 ca <名>。')
  add_intro    = @('Add accounts. Generate a token with `claude setup-token`; press Enter to finish.',
                   '添加账号。token 用 `claude setup-token` 生成；直接回车结束。')
  ask_name     = @('  account name (e.g. work, personal)', '  账号名（如 work、personal）')
  have_accs    = @('Existing accounts: {0}',        '已有账号：{0}')
  paste_token  = @('  paste token for {0} (hidden)', '  粘贴 {0} 的 token（不回显）')
  saved        = @('Saved {0} → {1}',               '已保存 {0} → {1}')
  verify_hint  = @('  ↳ verify with `ca test {0}`', '  ↳ 验证：`ca test {0}`')
  empty_token  = @('Empty token, nothing saved.',   'token 为空，未保存。')
  odd_token    = @('Heads up: setup-token values normally start with sk-ant-oat. Saved anyway.',
                   '提醒：setup-token 的 token 通常以 sk-ant-oat 开头，这个不是，仍然保存。')
  bad_chars    = @('Account names take letters, digits, _ and - only: {0}', '账号名只能用字母、数字、_、-：{0}')
  reserved     = @('{0} is a ca subcommand, pick another name.', '{0} 是 ca 的子命令，换一个名字。')
  no_account   = @('No account {0}. Have: {1}',     '没有账号 {0}，现有：{1}')
  none_yet     = @('No accounts yet — run `ca add <name>`.', '还没有账号，运行 `ca add <名>`。')
  ls_footer    = @('* = default account (ca use). Tokens last about a year.',
                   '* = ca use 选定的默认账号。token 有效期约一年。')
  no_accounts  = @('No accounts yet.',              '还没有账号。')
  no_claude_err= @('Cannot find the claude executable.', '找不到 claude。')
  removed      = @('Removed {0}',                   '已删除 {0}')
  not_git      = @('Not a git checkout, nothing to update: {0}', '不是 git clone 安装的，无法更新：{0}')
  cur_is       = @('Default account: {0}',          '当前默认账号：{0}')
  cur_none     = @('No default account — Claude uses the one you logged in with (/login).',
                   '未选定默认账号，用的是 /login 登录的账号。')
  use_off      = @('Cleared — back to the /login account. Quit VS Code completely and reopen it.',
                   '已取消，改回 /login 登录的账号；完全退出并重开 VS Code 后生效。')
  use_set      = @('Default account is now {0}',    '默认账号改为 {0}')
  use_hint     = @('  ↳ quit VS Code completely and reopen it; new terminals pick it up too',
                   '  ↳ 完全退出并重开 VS Code 生效；新开的终端也会用它')
  win_only     = @('This is the Windows build — on Linux and macOS run: ./ca install',
                   '这是 Windows 版；Linux / macOS 请运行：./ca install')
  no_push      = @('push is not available on Windows — clone the repo on the target machine, or use the bash build in WSL / Git Bash.',
                   'Windows 版不支持 push；在目标机器上 clone 本仓库，或在 WSL / Git Bash 里用 bash 版。')
  vscode_win   = @('Nothing to configure on Windows: pick an account with `ca use <name>`, then quit VS Code completely and reopen it.',
                   'Windows 上不需要额外设置：用 `ca use <名>` 选账号，然后完全退出并重开 VS Code。')
  cont_head    = @('{0} will continue this conversation:', '{0} 将接着这个会话：')
  cont_ago     = @('  last active {0}',              '  最后活动于 {0}')
  cont_ask     = @('Enter to continue · Ctrl-C to cancel', '回车继续 · Ctrl-C 取消')
  usage_5h     = @('  5h    {0} {1,3}%   resets {2}', '  5 小时  {0} {1,3}%   {2} 重置')
  usage_7d     = @('  week  {0} {1,3}%   resets {2}', '  本周    {0} {1,3}%   {2} 重置')
  usage_fail   = @('  could not read limits ({0})', '  读不到额度（{0}）')
  who_shell    = @('This session runs as {0}',            '当前会话用的是 {0}')
  who_login    = @('This shell uses the /login account {0}', '当前 shell 用的是 /login 的账号 {0}')
  who_vscode   = @('  ↳ default account is {0}',          '  ↳ 默认账号是 {0}')
  path_added   = @('Added {0} to your user PATH.',  '已把 {0} 加入用户 PATH。')
  git_refuse   = @('{0} sits inside a git repository — refusing to keep tokens there. Set CA_DIR elsewhere.',
                   '{0} 在 git 仓库里，拒绝在这里存 token。请用 CA_DIR 换个位置。')
}
function T([string]$k) {
  $pair = $Msg[$k]
  $s = if ($Zh) { $pair[1] } else { $pair[0] }
  if ($args.Count -gt 0) { return ($s -f $args) }
  return $s
}

# --- helpers -------------------------------------------------------------------------------------
function TokFile([string]$n) { Join-Path $CaDir "$n.token" }
function Get-Names {
  if (-not (Test-Path -LiteralPath $CaDir)) { return @() }
  @(Get-ChildItem -LiteralPath $CaDir -Filter '*.token' -File | Sort-Object Name | ForEach-Object { $_.BaseName })
}
function Get-Current {
  $f = Join-Path $CaDir 'current'
  if (Test-Path -LiteralPath $f) { ([IO.File]::ReadAllText($f)).Trim() } else { '' }
}
function Get-Token([string]$n) { ([IO.File]::ReadAllText((TokFile $n))).Trim() }
function Need-Acc([string]$n) {
  if (-not (Test-Path -LiteralPath (TokFile $n))) { Die (T no_account "$A$n$X" ((Get-Names) -join ' ')) }
}
function Test-Name([string]$n) {
  if ($n -notmatch '^[A-Za-z0-9_-]+$') { Die (T bad_chars $n) }
  if ($Reserved -contains $n.ToLower()) { Die (T reserved $n) }
}
function In-Git([string]$d) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return $false }
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $null = & git -C $d rev-parse --is-inside-work-tree 2>$null; return ($LASTEXITCODE -eq 0) }
  finally { $ErrorActionPreference = $old }
}
function Restrict-ToMe([string]$path, [bool]$isDir) {
  if ($OnWindows) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $grant = if ($isDir) { "*${sid}:(OI)(CI)F" } else { "*${sid}:F" }
    $null = & icacls $path /inheritance:r /grant:r $grant 2>&1
  } else {
    & chmod ($(if ($isDir) { '700' } else { '600' })) $path
  }
}

# tokens must never land inside a git repository
function Ensure-CaDir {
  $p = $CaDir
  while (-not (Test-Path -LiteralPath $p)) { $p = Split-Path -Parent $p; if (-not $p) { break } }
  if ($p -and (In-Git $p)) { Die (T git_refuse $CaDir) }
  if (-not (Test-Path -LiteralPath $CaDir)) { $null = New-Item -ItemType Directory -Force -Path $CaDir }
  Restrict-ToMe $CaDir $true
}

function Set-UserToken([string]$value) {
  # Windows: the VS Code extension cannot use a .cmd as claudeProcessWrapper, so the account is
  # carried by a user-level environment variable instead. VS Code picks it up on a full restart.
  if ($OnWindows) { [Environment]::SetEnvironmentVariable('CLAUDE_CODE_OAUTH_TOKEN', $value, 'User') }
}

# --- commands ------------------------------------------------------------------------------------
function Cmd-Add([string]$n, [string[]]$piped) {
  if (-not $n) { Die 'usage: ca add <name>' }
  Test-Name $n
  Ensure-CaDir
  if ($piped -and $piped.Count -gt 0) {
    $t = $piped[0]
  } elseif ([Console]::IsInputRedirected) {
    $t = [Console]::In.ReadLine()
  } else {
    $ss = Read-Host -AsSecureString (T paste_token "$A$n$X")
    $t = (New-Object System.Management.Automation.PSCredential 'x', $ss).GetNetworkCredential().Password
  }
  if ($null -eq $t) { $t = '' }
  $t = ($t -replace '\s', '')
  if (-not $t) { Die (T empty_token) }
  if (-not $t.StartsWith('sk-ant-oat')) { Note (T odd_token) }
  $f = TokFile $n
  [IO.File]::WriteAllText($f, "$t`n", (New-Object System.Text.UTF8Encoding $false))
  Restrict-ToMe $f $false
  Ok (T saved "$A$n$X" $f)
  Note (T verify_hint $n)
}

function Cmd-Ls {
  $ns = Get-Names
  if ($ns.Count -eq 0) { Note (T none_yet); return }
  $cur = Get-Current
  foreach ($n in $ns) {
    $f = TokFile $n
    $mark = if ($n -eq $cur) { "$A*$X " } else { '  ' }
    $tok = Get-Token $n
    $tail = if ($tok.Length -gt 6) { $tok.Substring($tok.Length - 6) } else { $tok }
    $date = (Get-Item -LiteralPath $f).LastWriteTime.ToString('yyyy-MM-dd')
    Write-Output ("{0}{1}{2,-14}{3} {4}…{5}{3}  {4}{6}{3}" -f $mark, $B, $n, $X, $D, $tail, $date)
  }
  Note (T ls_footer)
}

function Cmd-Test([string[]]$ns) {
  if (-not $ns -or $ns.Count -eq 0) { $ns = Get-Names }
  if ($ns.Count -eq 0) { Die (T no_accounts) }
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Die (T no_claude_err) }
  $bad = 0
  foreach ($n in $ns) {
    Need-Acc $n
    $ErrorActionPreference = 'Continue'
    $old = $env:CLAUDE_CODE_OAUTH_TOKEN
    $env:CLAUDE_CODE_OAUTH_TOKEN = Get-Token $n
    try { $out = ('' | & claude -p 'reply with the single word ok' --max-turns 1 2>&1 | Out-String) }
    finally {
      if ($null -eq $old) { Remove-Item Env:CLAUDE_CODE_OAUTH_TOKEN -ErrorAction SilentlyContinue } else { $env:CLAUDE_CODE_OAUTH_TOKEN = $old }
      $ErrorActionPreference = 'Stop'
    }
    if ($out -match '(?i)api error|failed to authenticate|invalid|limit' -or $out -notmatch '(?i)ok') {
      $line = ($out -split "`r?`n" | Where-Object { $_ -match '(?i)error|fail|invalid|limit' } | Select-Object -First 1)
      Write-Output ("$R✗$X {0}{1,-14}{2} {3}" -f $B, $n, $X, $line); $bad = 1
    } else {
      Write-Output ("$G✓$X {0}{1,-14}{2}" -f $B, $n, $X)
    }
  }
  exit $bad
}

function Cmd-Rm([string]$n) {
  if (-not $n) { Die 'usage: ca rm <name>' }
  Need-Acc $n
  Remove-Item -LiteralPath (TokFile $n) -Force
  Say (T removed $n)
  if ((Get-Current) -eq $n) { Cmd-Use '--off' }
}

function Cmd-Use([string]$n) {
  $f = Join-Path $CaDir 'current'
  if (-not $n) {
    $c = Get-Current
    if ($c) { Say (T cur_is "$A$c$X") } else { Note (T cur_none) }
    return
  }
  if ($n -eq '--off') {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
    Set-UserToken $null
    if ($OnWindows) { [Environment]::SetEnvironmentVariable('CA_ACCOUNT', $null, 'User') }
    Say (T use_off)
    return
  }
  Need-Acc $n
  [IO.File]::WriteAllText($f, "$n`n", (New-Object System.Text.UTF8Encoding $false))
  Set-UserToken (Get-Token $n)
  if ($OnWindows) { [Environment]::SetEnvironmentVariable('CA_ACCOUNT', $n, 'User') }
  Ok (T use_set "$A$n$X")
  Note (T use_hint)
}

# `ca who` answers "whose quota am I spending?", also from inside a running session:
# CA_ACCOUNT is inherited by Claude Code and by the shell it runs commands in.
# --continue is otherwise a blind jump: show which conversation is about to be resumed.
# Session files are Claude Code's own: ~/.claude/projects/<cwd with separators as ->/<uuid>.jsonl
function Show-ContinuePreview([string]$n) {
  $dir = Join-Path (Join-Path (Join-Path $HOME '.claude') 'projects') (((Get-Location).Path -replace '[\\/:.]', '-'))
  if (-not (Test-Path -LiteralPath $dir)) { return }
  $f = Get-ChildItem -LiteralPath $dir -Filter '*.jsonl' -File -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }
  $title = ''; $prompt = ''
  foreach ($line in [IO.File]::ReadLines($f.FullName)) {
    if ($line -notmatch 'aiTitle|lastPrompt') { continue }
    try { $e = $line | ConvertFrom-Json } catch { continue }
    if ($e.aiTitle)    { $title  = $e.aiTitle }
    if ($e.lastPrompt) { $prompt = $e.lastPrompt }
  }
  if (-not $title) { $title = $f.BaseName.Substring(0, 8) }
  $span = (Get-Date) - $f.LastWriteTime
  $ago = if ($span.TotalMinutes -lt 1) { '{0:N0}s' -f $span.TotalSeconds }
         elseif ($span.TotalHours -lt 1) { '{0:N0}m' -f $span.TotalMinutes }
         elseif ($span.TotalDays -lt 1) { '{0:N0}h' -f $span.TotalHours }
         else { '{0:N0}d' -f $span.TotalDays }
  Head (T cont_head "$A$n$X")
  Say "  $B$title$X"
  Note (T cont_ago $ago)
  if ($prompt) { Note ('  "{0}"' -f (($prompt -replace '\s+', ' ').Trim().PadRight(1).Substring(0, [Math]::Min(70, $prompt.Length)))) }
  if (-not [Console]::IsInputRedirected -and -not $env:CA_YES) { Note (T cont_ask); $null = [Console]::ReadLine() }
}

# Rate limits are not in the token, but the API reports them in response headers, so one
# minimal call per account (a single Haiku token) reads them back.
function Show-Bar([int]$pct) {
  $filled = [Math]::Min(10, [Math]::Ceiling($pct / 10.0))
  ('█' * $filled) + ('░' * (10 - $filled))
}

function Cmd-Usage([string[]]$ns) {
  if (-not $ns -or $ns.Count -eq 0) { $ns = Get-Names }
  if ($ns.Count -eq 0) { Die (T no_accounts) }
  $body = '{"model":"claude-haiku-4-5-20251001","max_tokens":1,"system":[{"type":"text","text":"You are Claude Code, Anthropic''s official CLI for Claude."}],"messages":[{"role":"user","content":"hi"}]}'
  foreach ($n in $ns) {
    Need-Acc $n
    Head $n
    $headers = $null
    try {
      $r = Invoke-WebRequest -Uri 'https://api.anthropic.com/v1/messages' -Method Post -TimeoutSec 60 `
             -Headers @{ Authorization = "Bearer $(Get-Token $n)"; 'anthropic-version' = '2023-06-01'; 'anthropic-beta' = 'oauth-2025-04-20' } `
             -ContentType 'application/json' -Body $body -UseBasicParsing
      $headers = $r.Headers
    } catch {
      Note (T usage_fail $_.Exception.Message); continue
    }
    $any = $false
    foreach ($pair in @(@('5h', 'usage_5h'), @('7d', 'usage_7d'))) {
      $u = $headers["anthropic-ratelimit-unified-$($pair[0])-utilization"]
      $res = $headers["anthropic-ratelimit-unified-$($pair[0])-reset"]
      if (-not $u) { continue }
      if ($u -is [array]) { $u = $u[0] }
      if ($res -is [array]) { $res = $res[0] }
      $pct = [int][Math]::Round([double]$u * 100)
      $when = if ($res) { ([DateTimeOffset]::FromUnixTimeSeconds([int64]$res)).LocalDateTime.ToString('MMM dd HH:mm') } else { '?' }
      Say (T $pair[1] (Show-Bar $pct) $pct $when)
      $any = $true
    }
    if (-not $any) { Note (T usage_fail 'no rate-limit headers') }
  }
}

function Cmd-Who {
  if ($env:CA_ACCOUNT) {
    Say "$A●$X $(T who_shell "$B$($env:CA_ACCOUNT)$X")"
  } else {
    $email = ''
    if (Get-Command claude -ErrorAction SilentlyContinue) {
      $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
      try { $j = (& claude auth status --json 2>$null | Out-String); if ($j) { $email = ($j | ConvertFrom-Json).email } } catch { }
      finally { $ErrorActionPreference = $old }
    }
    if (-not $email) { $email = '/login' }
    Say "$A●$X $(T who_login "$B$email$X")"
  }
  $cur = Get-Current
  if ($cur) { Note (T who_vscode $cur) }
}

function Cmd-Update {
  if (-not (In-Git $SrcDir)) { Die (T not_git $SrcDir) }
  & git -C $SrcDir pull --ff-only
  exit $LASTEXITCODE
}

function Cmd-Install {
  if (-not $OnWindows) { Die (T win_only) }
  Ensure-CaDir
  $null = New-Item -ItemType Directory -Force -Path $BinDir
  $impl = Join-Path $BinDir 'ca-win.ps1'
  if ($Self -ne $impl) {
    if (In-Git $SrcDir) {
      # git checkout: forwarder in BinDir, so `ca update` updates what actually runs
      $fwd = "# generated by ``ca install`` — forwards to the git checkout`r`n& '$($Self -replace "'", "''")' @args`r`nexit `$LASTEXITCODE`r`n"
      [IO.File]::WriteAllText($impl, $fwd, (New-Object System.Text.UTF8Encoding $true))
      Head (T installed (Join-Path $BinDir 'ca.cmd')); Note (T via_repo $Self)
    } else {
      Copy-Item -LiteralPath $Self -Destination $impl -Force
      Head (T installed (Join-Path $BinDir 'ca.cmd'))
    }
  }
  # the shim stays ASCII and uses %~dp0, so non-ASCII user names cannot break cmd parsing
  $cmd = "@powershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0ca-win.ps1`" %*`r`n"
  [IO.File]::WriteAllText((Join-Path $BinDir 'ca.cmd'), $cmd, (New-Object System.Text.ASCIIEncoding))

  # write PATH through the registry to keep %VAR% entries intact (SetEnvironmentVariable expands them)
  $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
  $userPath = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  if (($userPath -split ';') -notcontains $BinDir) {
    $key.SetValue('Path', (($userPath.TrimEnd(';') + ";$BinDir").TrimStart(';')), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    # setting and clearing a variable broadcasts WM_SETTINGCHANGE so new terminals see the new PATH
    [Environment]::SetEnvironmentVariable('CA_PATH_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('CA_PATH_REFRESH', $null, 'User')
    Note (T path_added $BinDir)
  }
  $key.Close()
  if (($env:Path -split ';') -notcontains $BinDir) { $env:Path = "$env:Path;$BinDir" }

  Note (T token_dir $CaDir)
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Note (T no_claude) }

  if (-not [Console]::IsInputRedirected -and -not $MyInvocation.ExpectingInput) {
    $ns = Get-Names
    if ($ns.Count -gt 0) { Note (T have_accs ($ns -join ' ')) }
    Say ''
    Head (T add_intro)
    while ($true) {
      $n = Read-Host (T ask_name)
      if (-not $n) { break }
      try { & powershell -NoProfile -ExecutionPolicy Bypass -File $Self add $n } catch { }
    }
  }
  Note (T path_hint)
}

function Show-Usage {
  $cur = Get-Current
  Write-Output "$A✳$X ${B}ca$X — switch between your own Claude subscription accounts`n"
  Write-Output "  ${B}ca <name>$X [claude args]   run Claude Code as that account, e.g. ca work --continue"
  Write-Output "  ${B}ca install$X                install the command, then add accounts interactively"
  Write-Output "  ${B}ca add$X <name>             store a token from ``claude setup-token``"
  Write-Output "  ${B}ca ls$X                     list accounts"
  Write-Output "  ${B}ca test$X [name...]         check the tokens with one real call"
  Write-Output "  ${B}ca rm$X <name>              forget an account"
  Write-Output "  ${B}ca who$X                    show which account this session is spending"
  Write-Output "  ${B}ca usage$X [name...]        5-hour and weekly limits, per account"
  Write-Output "  ${B}ca use$X [name|--off]       set the default account (VS Code, new terminals)"
  Write-Output "  ${B}ca update$X                 git pull this checkout`n"
  Note ("tokens: $CaDir" + $(if ($cur) { "   |   default: $cur" } else { '' }))
  Note 'docs: https://github.com/Hliudi/Claude-Account-Management'
}

$sub  = if ($args.Count -gt 0) { [string]$args[0] } else { '' }
# assign in two steps: returning a one-item array from an if-block unwraps it to a string,
# and splatting a string passes it one character at a time
$rest = @()
if ($args.Count -gt 1) { $rest = @($args[1..($args.Count - 1)]) }

switch -Exact ($sub) {
  { $_ -in @('', '-h', '--help', 'help') } { Show-Usage; exit 0 }
  'install' { Cmd-Install; exit 0 }
  'add'     {
    # $MyInvocation.ExpectingInput tells us a pipeline is attached; only then may we read it,
    # because enumerating $input blocks until stdin closes — which never happens in a terminal.
    $piped = if ($MyInvocation.ExpectingInput) { @($input | ForEach-Object { "$_" }) } else { @() }
    Cmd-Add ([string]($rest | Select-Object -First 1)) $piped
    exit 0
  }
  { $_ -in @('ls', 'list') } { Cmd-Ls; exit 0 }
  'test'    { Cmd-Test ([string[]]$rest) }
  'rm'      { Cmd-Rm ([string]($rest | Select-Object -First 1)); exit 0 }
  'use'     { Cmd-Use ([string]($rest | Select-Object -First 1)); exit 0 }
  { $_ -in @('who','whoami') } { Cmd-Who; exit 0 }
  'usage'   { Cmd-Usage ([string[]]$rest); exit 0 }
  'update'  { Cmd-Update }
  'vscode'  { Note (T vscode_win); exit 0 }
  'push'    { Die (T no_push) }
  default {
    Need-Acc $sub
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Die (T no_claude_err) }
    if ($rest -contains '--continue' -or $rest -contains '-c') { Show-ContinuePreview $sub }
    # the token lives in this process only and is restored afterwards
    $old = $env:CLAUDE_CODE_OAUTH_TOKEN
    $oldAcc = $env:CA_ACCOUNT
    $env:CLAUDE_CODE_OAUTH_TOKEN = Get-Token $sub
    $env:CA_ACCOUNT = $sub
    try { $Host.UI.RawUI.WindowTitle = "claude · $sub" } catch { }
    try { & claude @rest }
    finally {
      if ($null -eq $old) { Remove-Item Env:CLAUDE_CODE_OAUTH_TOKEN -ErrorAction SilentlyContinue } else { $env:CLAUDE_CODE_OAUTH_TOKEN = $old }
      if ($null -eq $oldAcc) { Remove-Item Env:CA_ACCOUNT -ErrorAction SilentlyContinue } else { $env:CA_ACCOUNT = $oldAcc }
    }
    exit $LASTEXITCODE
  }
}

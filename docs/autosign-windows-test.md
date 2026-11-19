# 每日自动签到 Windows 验收步骤（供 agent 执行）

本文件给在 Windows 本机执行验收的 agent 阅读。按顺序执行，每步记录结果，最后按“报告模板”汇报。
功能说明见 [game-signin.md](game-signin.md#每日自动签到windows-任务计划程序)。

## 安全规则（必须遵守）

- **不要输出、复制、记录 token / did**：不要打印 `localStorage`、`settings.json` 中的 `token`/`did` 字段原文，不要把它们写进报告、日志或提交。只按本文方法检查格式。
- **不要发送短信验证码、不要重新登录库街区**。复用用户浏览器里已有的登录。
- **真实签到写入每天最多发生一次**：程序会先查询状态，今日已签到时不会再写入。不要绕过这一机制，不要直接调用库洛接口。
- **不要修改源代码、不要提交 `backend/data/`**。发现问题只记录并汇报，修改代码需先征得用户同意。
- **自己启动的服务进程，结束时必须关闭**，并确认 8000 端口已释放（历史上出现过残留实例导致 WinError 10048）。不要结束不是你启动的进程。
- 需要用户在浏览器里点击时（见第 5 步），暂停并请用户操作，不要尝试读取浏览器凭证代替。

## 0. 准备

在 PowerShell 中，仓库根目录执行：

```powershell
git fetch
git switch codex/wuwa-game-signin
git pull --ff-only
git log --oneline -3          # 记录 HEAD 提交号，应包含 "feat: add Windows daily auto sign-in via Task Scheduler"
```

确定 Python 解释器：读取仓库根目录的 `start.bat`（机器专用文件，未提交），找出它使用的 conda 环境 `python.exe`。下文用 `$PY` 表示：

```powershell
$PY = 'D:\DevTools\anaconda3\envs\wuwawebtool\python.exe'   # 以 start.bat 实际内容为准
& $PY --version                                             # 预期 Python 3.12.x
Test-Path (Join-Path (Split-Path $PY) 'pythonw.exe')        # 记录：计划任务会优先用 pythonw.exe
$TASK = 'WuwaWebTool 鸣潮每日签到'
$DATA = 'backend\data\autosign'                             # 若设置了 WUWA_DATA_PATH，则为 $env:WUWA_DATA_PATH\autosign
```

确认 8000 端口当前没有被占用（`Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue`）。若已有用户自己的实例在运行，复用它，跳过第 3 步的启动，第 12 步也不要关闭它。

## 1. 离线测试（不联网、不使用真实凭证）

```powershell
cd backend
& $PY -m unittest discover -s tests -v       # 预期 38 项全部 OK（含 tests/test_autosign.py 16 项）
cd ..\frontend
npm ci
npm test                                     # 预期 2 个文件 16 项通过
npm run build                                # 预期 vue-tsc 无错误、构建成功；产物 frontend/dist 供服务使用
cd ..
```

## 2. Windows 自检（不联网）

```powershell
cd backend
& $PY autosign.py --self-test
echo "exit=$LASTEXITCODE"
cd ..
```

预期输出 `PASS DPAPI 加解密`、`PASS 当前用户 SID 与任务 XML`、`PASS 任务计划程序可查询（已安装=False…）`、`SELF-TEST PASSED`，退出码 0。
记录 `INFO 解释器` 和 `INFO 数据目录` 两行。

## 3. 启动服务

用用户平时的方式启动（运行 `start.bat`），或在新窗口：`cd backend; & $PY main.py`。记下你启动的进程 PID。

```powershell
curl.exe -s http://localhost:8000/api/health        # 预期 "status":"ok"
```

## 4. 接口访问限制

```powershell
curl.exe -s http://localhost:8000/api/game/auto-sign/status
# 预期 200，"supported":true，"enabled":false（若用户之前已开启则为 true，记录下来）

curl.exe -s -o NUL -w "%{http_code}\n" -H "X-Forwarded-For: 127.0.0.1" http://localhost:8000/api/game/auto-sign/status
# 预期 403

curl.exe -s -o NUL -w "%{http_code}\n" -X POST -H "Origin: https://evil.example" http://localhost:8000/api/game/auto-sign/disable
# 预期 403（且不会关闭任何东西）
```

可选：从局域网另一台设备访问 `http://<本机IP>:8000/api/game/auto-sign/status`，预期 403。

## 5. 在网页上开启（需要用户操作）

请用户在**已登录库街区**的浏览器里打开 `http://localhost:8000`，选中鸣潮国服角色，然后：

1. 确认左侧“鸣潮游戏签到”下方出现“每日自动签到”卡片，显示“未开启”。
2. 时间选择**当前时间往后约 3 分钟**（用于第 8 步验证自动触发），点击“开启自动签到”。
3. 预期提示“已开启，每天 HH:MM 自动签到”，状态为“已开启 · 每天 HH:MM · UID …”，没有红色警告。

记下设置的时间 `$T`（如 `14:23`）。如果提示失败，记录完整提示文字（不含凭证）并停止后续步骤。

## 6. 检查计划任务

```powershell
schtasks /Query /TN $TASK /V /FO LIST
schtasks /Query /TN $TASK /XML > "$env:TEMP\wuwa-task.xml"
Select-String -Path "$env:TEMP\wuwa-task.xml" -Pattern 'StartBoundary|StartWhenAvailable|LogonType|<Command>|<Arguments>|<WorkingDirectory>'
Remove-Item "$env:TEMP\wuwa-task.xml"
```

预期：

- `StartBoundary` 时间为 `$T`；`StartWhenAvailable` 为 `true`；`LogonType` 为 `InteractiveToken`。
- `Command` 为 `$PY` 同目录的 `pythonw.exe`（若该文件不存在则为 `python.exe`）。
- `Arguments` 为带引号的 `…\backend\autosign.py` 完整路径；`WorkingDirectory` 为 `…\backend`。

## 7. 检查凭证加密（不要输出凭证）

```powershell
$s = Get-Content "$DATA\settings.json" -Raw | ConvertFrom-Json
$s | Select-Object version, uid, time, protection, updated_at      # 只看这些字段
$s.token.StartsWith('AQAAANCMnd8BFdERjHoAwE/Cl+s')                 # 预期 True（DPAPI 密文的固定开头）
$s.did.StartsWith('AQAAANCMnd8BFdERjHoAwE/Cl+s')                   # 预期 True
Remove-Variable s
```

预期 `protection` 为 `dpapi-current-user`，`time` 为 `$T`。

## 8. 自动触发（真实签到）

等待到 `$T` 之后约 1 分钟（不要手动运行）。然后：

```powershell
schtasks /Query /TN $TASK /V /FO LIST | Select-String '上次运行时间|上次结果|Last Run Time|Last Result'
Get-Content "$DATA\autosign.log" -Tail 5
Get-Content "$DATA\last_run.json"
```

预期：

- 上次结果 `0`。
- 日志最后一行形如 `UID xxxxx：鸣潮游戏奖励签到成功，本期累计 N 天（signed，尝试 1 次）`；如果今天已经手动签过，则为 `今日已签到（already_signed …）`。两者都算通过。
- `last_run.json` 中 `success` 为 `true`。
- 刷新网页后，卡片显示“上次运行 …”；上方手动签到卡片显示“今日已签到”。

**如果失败**（上次结果非 0、日志里是 `network_error` 或没有日志），用控制台解释器手动运行一次以看到报错：

```powershell
cd backend
& $PY autosign.py; echo "exit=$LASTEXITCODE"
Get-Content data\autosign\autosign.log -Tail 3
cd ..
```

区分并记录：

- 用 `python.exe` 手动运行成功，但计划任务（`pythonw.exe`、未激活 conda 环境）失败 → 很可能是 conda 环境未激活导致的 DLL/SSL 问题。记录日志内容和任务上次结果代码，汇报给用户，不要自行改代码。
- 两种方式都失败 → 记录日志中的状态（`login_expired` / `captcha_required` / `network_error` 等）。

## 9. 再次运行不会重复领取

```powershell
schtasks /Run /TN $TASK
# 等待约 30 秒
Get-Content "$DATA\autosign.log" -Tail 1      # 预期“今日已签到（already_signed，尝试 1 次）”
```

## 10. 修改时间

在网页卡片中把时间改为另一个值（例如 `09:00`），点击“保存设置”。预期提示成功、卡片显示新时间；再用第 6 步命令确认 `StartBoundary` 已变为新时间。

## 11. 关闭

在网页卡片中点击“关闭”并确认弹窗。预期：

```powershell
schtasks /Query /TN $TASK            # 预期报错：找不到任务
Test-Path "$DATA\settings.json"      # 预期 False
Test-Path "$DATA\last_run.json"      # 预期 False
```

卡片显示“未开启”。`autosign.log` 会保留（不含凭证）。

**然后询问用户**：是否按其希望的时间重新开启自动签到。用户给出时间则在网页上重新开启并重复第 6 步检查；用户不需要则保持关闭。

## 12. 收尾

- 关闭你在第 3 步启动的服务进程，确认 `Get-NetTCPConnection -LocalPort 8000 -State Listen` 没有结果。用户原本就在运行的实例不要关闭。
- 确认 `git status --short` 没有新增的已跟踪改动（`frontend/dist`、`backend/data` 已被忽略）。
- 不要提交任何文件，除非用户要求。

## 报告模板

```markdown
## 自动签到 Windows 验收报告
- 提交：<HEAD 提交号>
- 解释器：<$PY>，pythonw.exe 存在：是/否
- Windows 版本：<winver 或 [Environment]::OSVersion>

| 步骤 | 结果 | 备注（不含凭证） |
|---|---|---|
| 1 离线测试 | 通过/失败 | 后端 x/38，前端 x/16，构建 |
| 2 自检 | | |
| 4 访问限制 | | 200 / 403 / 403 |
| 5 网页开启 | | |
| 6 计划任务 | | Command / StartBoundary |
| 7 凭证加密 | | |
| 8 自动触发 | | 上次结果代码，日志末行 |
| 9 不重复领取 | | |
| 10 修改时间 | | |
| 11 关闭 | | 最终是否重新开启、时间 |
| 12 收尾 | | 端口已释放 |

问题与日志摘录：
<失败时的日志末几行、任务上次结果代码、页面提示文字>
```

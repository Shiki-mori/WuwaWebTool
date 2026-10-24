# 鸣潮国服游戏奖励签到

## 使用

在本机运行 `start.bat`，打开 `http://localhost:8000`，沿用原来的库街区登录。
首页左侧“鸣潮游戏签到”会自动查询当前所选 UID 的今日状态。点击“领取游戏签到奖励”完成一次手动签到。
“刷新状态”只查询，不领取。成功或今日已签到后，领取按钮禁用；北京时间跨日后重新查询。
没有后台定时任务、社区签到、浏览、点赞、分享、战双签到或补签。

登录过期时，卡片提供重新登录链接；网络异常或结果不确定时，先点击“刷新状态”。
卡片独立于角色详情和评分加载，评分资源暂时不可用也不影响签到。
界面只展示签到写入响应明确提供的物品名称/数量，不将日历奖励配置或历史记录当作本次领取凭据。

## 实现

- `backend/core/game_signin.py`：绑定校验、日历查询、单次签到、状态归一化和同账号同 UID 并发串行化。
- `backend/api/signin.py`：`POST /api/game/sign-in/status` 与 `POST /api/game/sign-in`；请求使用现有 `uid`、`token`、`did`，可选 `server_id`。凭证不进入 URL。
- `backend/core/api/requests.py`：复用 aiohttp 请求、代理配置和现有 URL，签到启用严格响应验证，禁用自动重试和验证码求解。
- `frontend/src/components/index/SignInCard.vue`：手动按钮、今日状态、累计天数、错误及可选奖励；切换账号丢弃迟到响应。
- `frontend/src/composables/useApi.ts`：签到专用 45 秒客户端超时。请求超时可能已经到达官方，因此不自动再次领取。
- `frontend/src/stores/character.ts`：角色列表排除明确标记为其他游戏的条目，保留已选鸣潮 UID。
- 登录及请求诊断不再输出手机号、验证数据、请求头和完整上游响应；签到参数校验失败也不回显提交值。

签到每次先向官方 `/gamer/role/list` 查询 `gameId=3`，仅匹配用户当前选择的 `roleId`，并核验国服 `serverId`。
不使用列表第一项代替当前 UID，不把库街区 `userId` 当作游戏角色 ID。
日历成功且明确返回 `isSigIn=false` 才允许写入。写入 `gameId=3`、绑定的服务器和角色 ID、北京时间两位数月份 `reqMonth`。
成功响应和官方 `1511`（今日已签到）分别处理；`220` 登录过期、`1513` 角色异常及其他失败均不伪造成功。
严格模式拒绝缺失状态码、非预期类型及相互矛盾的成功字段。

## 上游核查

2026-10-24 拉取并检查三个仓库当时的默认分支源码和许可证；只参考协议及结果处理，没有引入其机器人框架或调度器。

| 项目 | 核查提交 | 许可证 | 结论 |
| --- | --- | --- | --- |
| [RoverSign](https://github.com/Loping151/RoverSign/tree/ede87884ce8884c802fd7dca8a83e9212fc91d17) | `ede87884` | GPL-3.0 | `RoverSign/utils/api/requests.py` 的 `sign_in`、`sign_in_task_list` 适用；保留 token/did、角色和服务器参数，改为本项目的无数据库凭证调用。 |
| [Kuro-autosignin](https://github.com/mxyooR/Kuro-autosignin/tree/a3347962dadd036ea898f03c45ab273df0a00d15) | `a3347962` | GPL-3.0 | 核查 `game_sign_in.py`、`constants.py`、`http_client.py`；采用 1511/1513/220 语义。不复制其历史奖励列表第一条作为本次奖励的做法。 |
| [kurobbs_auto_checkin](https://github.com/leeezep/kurobbs_auto_checkin/tree/1eca27199db0b21cebef45fad9973ffd43380e8c) | `1eca2719` | MIT | 核实相同游戏签到地址和北京时间月份。其默认绑定角色选择不符合本任务，未采用。 |

本项目现有许可证为 GPL-3.0。日历使用项目已保留的 `SIGNIN_TASK_LIST_URL`，写入使用已保留的 `SIGNIN_URL`。
原有 `get_sign_in_init` 依赖数据库取凭证，Web 登录凭证来自浏览器，因此复用请求封装、URL 和参数格式，采用无数据库的日历调用。

## 验证命令

在 `backend` 下：

```powershell
& 'D:\DevTools\anaconda3\envs\wuwawebtool\python.exe' -m unittest discover -s tests -v
```

在 `frontend` 下：

```powershell
npm ci
npm test
npm run build
npm audit
```

后端 17 项离线测试覆盖只读状态、成功、已签到、1511、登录过期、错误角色/游戏/服务器、验证码、畸形响应、网络异常、写后查询失败、并发提交及敏感参数不回显。
前端 9 项测试覆盖只读初始化、重复点击、奖励显示、已签到禁用、过期链接、写入超时后刷新、账号切换、北京时间跨日（含在途请求）及文本转义。
离线测试没有真实凭证，不访问库洛接口。
最终 TypeScript 检查和生产构建通过，锁定依赖的 `npm audit` 为 0 漏洞。

## 本机验收记录

- 2026-10-24 在用户原已登录的 Chrome `localhost:8000` 页面验收，保留原登录。
- 签到前官方日历显示“今日未签到，本期累计 19 天”。
- 仅执行一次真实游戏签到：显示“鸣潮游戏奖励签到成功，本期累计 20 天”。
- 随后只读刷新：显示“今日已签到，本期累计 20 天”，领取按钮禁用。
- 本次未展示奖励明细，没有根据日历配置推断物品。
- 重新请求角色详情和声骸评分均返回 HTTP 200，页面显示千咲综合评分 178.74 及五个声骸评分。
- `start.bat` 已实际重新启动服务；HTTP 首页与 `frontend/dist/index.html` 一致，不含 Vite 开发客户端。
- 最终 `/api/health` 返回 `init_ready=true`、`init_error=null`。
- 截图及本机运行日志位于被 Git 忽略的 `.task-output/`；不提交浏览器凭证、用户数据或本机日志。

## 限制

官方非公开接口未来可能变化。需要库街区安全验证或重新登录时由用户在官方/现有登录流程完成；不会尝试自动求解签到验证码。
并发锁仅作用于同一后端进程，多进程部署仍依赖官方“今日已签到”响应；网络失败不保证请求未到达官方，须先查询状态。
本次复用已有登录，未发送新短信或重新输入验证码；登录过期等失败路径由离线测试验证。
本机启动器是原先通过 `.git/info/exclude` 排除的机器专用文件，未改变或提交其绝对路径。

## 重复启动与端口占用

`start.bat` 调用的 `backend/main.py` 会在加载应用和下载资源前检查监听端口。
已有健康的 WuwaWebTool 实例时提示 `Already running on port 8000` 并正常退出新进程，浏览器直接访问已有服务即可。
其他程序占用或端口不可用时，提示先释放端口或通过 `PORT` 环境变量选择其他端口，不会停止已有程序。

2026-10-24 修复验收残留后台服务导致的 WinError 10048；实际验证首次启动及重复启动，并增加 5 项端口/健康响应检查测试，后端共 22 项通过。
验证完成后已停止本次验收启动的进程、确认 8000 端口释放，避免后台验收实例影响用户下次启动。

# 闲鱼收藏清单

协作开发、Agent 提交与合并规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

仅在本机运行的闲鱼收藏整理工具。收藏可编辑、加入比价队列、移除或标记已购；点击同步后统一提交取消收藏。先保存本地任务，再提交远端变更，失败可重试。

源码不含账号、Cookie、收藏数据或本机数据库。运行数据保存在 D:/AppData/XianyuWishlist，凭据仅由本机后端读取。

## 使用

双击 `Open-Wishlist.cmd`，或运行 `Start-Wishlist.ps1`。页面地址为 <http://127.0.0.1:8766>。启动器会复用已运行的服务；仅监听本机。没有配置开机启动或后台定时取消收藏。

- **读取收藏**：分页读取完整收藏，将新增商品加入清单；不提交取消收藏。只有分页结束、编号去重和总数核对均通过，才采纳本轮结果。
- **勾选**：保存比价任务，移到比价队列；同类型自动合并。型号、数字规格、版次、册次有差异时保守分开。可编辑“比价类型”手动合并或拆分。
- **编辑**：修改本地名称、备注和类型。刷新会保留这些内容，不修改闲鱼商品标题。
- **移除 / 已购**：从活动列表移至已处理记录，保留排除记录，避免后续重新导入。任务上的“已购，归档”作用于该类型的全部参考商品。
- **同步**：先读取最新收藏，再统一提交待取消收藏操作；重新完整读取收藏以确认结果。远端未确认、失败或中断时保留待同步状态，重试不会重复创建任务。
- **撤销**：远端提交前可撤销。提交后“恢复本地”只恢复本地清单，不会擅自重新收藏。
- **导出比价队列**：导出含购买要求、参考商品与报价的 JSON，作为下一轮搜索、搜集和比价的输入。当前版本不自动执行比价或购买。

需要在页面点击读取或同步来获取闲鱼变化；本地动作即时保存。服务每 10 分钟只读检查连接，接收并持久保存平台轮换的 Cookie，令牌失效时重新签名，Session 失效时尝试一次静默续期。网络失败按 30 秒至 10 分钟退避；需要扫码或安全验证时停止密集重试，每小时检查一次，并支持立即人工检查。

决策工作台的闲鱼页显示当前连接、最近成功时间及扫码重连入口。用原闲鱼账号扫码并在手机确认后，必须通过真实收藏接口读取才能显示已连接；其他账号不能替换既有清单绑定。平台强制登录或安全验证仍需本人完成，保活不能保证账号永久免验证。保活与扫码均不会取消收藏、购买或发送消息。

优先复用 Chrome 的现有登录：保存的会话失效时，通过已安装的微软官方 Playwright 扩展读取当前 Chrome Default 资料中的闲鱼 Cookie，核对原账号并通过收藏接口验证后，再保存为本应用会话。凭据只经过本机进程管道，不进入日志、前端或服务器。已保存的会话可在 Chrome 关闭后继续使用；需要重新借用浏览器会话时，Chrome 须已打开并登录原账号。服务不会启动或重启 Chrome，不自动安装扩展，也不读取其他站点的返回结果。

浏览器接入复用 Windows 用户环境中的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`，当前进程变量可覆盖；没有该凭据或扩展时沿用保存的会话和扫码入口。官方接入说明见 [Playwright Chrome Extension](https://github.com/microsoft/playwright/tree/main/packages/extension#readme)。扫码兼容平台实际使用的 `SCANED` 状态，并将未识别协议状态与真实安全验证分别提示。

## 安装与运行

使用 Python 3.14 / PowerShell 7，依赖写入 D 盘运行环境：

```powershell
$env:PIP_CACHE_DIR = 'D:\Caches\pip'
$env:TEMP = 'D:\Caches\pip\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
python -m venv 'D:\AppData\XianyuWishlist\venv'
& 'D:\AppData\XianyuWishlist\venv\Scripts\python.exe' -m pip install -r requirements.txt
npm ci --ignore-scripts --cache D:/Caches/npm
pwsh -NoProfile -File .\Start-Wishlist.ps1
```

默认从本机已授权的 `D:/AppData/XianyuHunterTrial/private/storage-state.json` 读取初始登录态，后续轮换原子写入本应用数据目录的 `login-state.json`，不覆盖初始来源。可用 `WISHLIST_LOGIN_STATE` 指定本应用专用的登录态文件。仅加载 Goofish 域 Cookie，固定账号绑定，账号变化时拒绝同步。`WISHLIST_DATA` 可指定独立数据库目录。初始化脚本仅用于导入已确认完整的同账号快照，已有清单不会覆盖。

后端不提供通用代理：只允许收藏列表、取消收藏和固定的官方登录/扫码端点，不跟随平台返回的任意重定向。会话 Cookie、扫码轮询凭据和登录 token 不返回页面；登录二维码只在登录期间提供给已授权的工作台会话，过期移除。修改接口有 Host、Origin 和随机请求令牌校验，正文以文本节点渲染。收藏图片直接请求闲鱼图片域，使用 no-referrer。

数据目录 `private` 仅授权当前 Windows 用户与 SYSTEM，数据库通过 SQLite 事务保存。每次同步前保留一份 `before-sync.sqlite3` 本地恢复点；该文件不代替长期备份。运行日志和数据库不进入 Git。

## 验证

```powershell
& 'D:\AppData\XianyuWishlist\venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
& 'D:\AppData\XianyuWishlist\venv\Scripts\python.exe' -m pytest -q
npm test
```

测试使用隔离数据库和模拟远端，覆盖事务持久化、自动合并、编辑保护、排除记录、已购、分页重复/不完整拒绝、失败重试、响应成功但远端未取消、账号不一致与本机接口保护。`tests.fixture_server:create_fixture` 供浏览器验收使用，无法连接闲鱼。`scripts/verify_render.py` 仅做本机只读截图，需另装 Playwright 并使用已安装的 Chrome。

## 接口依据

- HTTP 签名与会话复用固定版本 [DoLovya/pyxianyu](https://github.com/DoLovya/pyxianyu/tree/6d666037df8d007443c0d8a83df0b928b57d9a0b)，MIT。
- 收藏列表字段参考 [goofish-client 收藏接口](https://github.com/11273/goofish-client/blob/30b153653588d4d4e1d9038290b1e968525e5be4/docs/api/favor.md)，分页参数另经本机只读实测。
- 取消收藏 `com.taobao.idle.unfavor.item`、请求 `itemId`，交叉核对 [social-sdk-parent](https://github.com/isozone/social-sdk-parent/blob/a23419bf12233a7e2ebc80866aed0d23d14863db/social-sdk-xianyu/src/main/java/cn/net/rjnetwork/xianyu/api/XianyuCollectApiService.java) 和 [xianyu-instant-purchase-tool](https://github.com/magic007/xianyu-instant-purchase-tool/blob/f89552e9e63413cd02285c7885410fb8bd4e5b50/automation/service/auto_add_commodity/AutoAddCommodity.py)。未调用猜测的替代接口。

真实账号已验证完整读取；取消收藏目前通过模拟远端验证，首次真实提交需以页面勾选后的同步与回读结果为准。

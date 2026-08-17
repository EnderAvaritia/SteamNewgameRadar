# Steam 新游戏监控

监控 Steam 新游戏上线的 Python 脚本。两条监控线，一套引擎：

1. **发行商监控**：监控指定发行商是否有新游戏发行，含"公布新游戏但无发售日"的情报跟踪（公布发售日时也会提醒）。
2. **游戏监控**：监控你感兴趣的游戏是否临近发售 / 已发售。

多阶段提醒（默认发售前 14 天、发售前 7 天、发售后 3 天），通过 **ntfy / Server酱 / PushPlus** 推送到手机，并每次运行生成 Markdown 报告兜底。

## 特性

- 免费、无需 Steam API Key（Storefront 免费接口）
- 检查点可配置，`+` 表示发售前、`-` 表示发售后（默认 `[+14, +7, -3]`）
- 发售日变动自适应（跳票自动重算提醒计划）
- 发售日未定的游戏持续跟踪，公布发售日即提醒
- 通知格式模板化：全局默认 + 每渠道覆盖，8 个变量（游戏名/发行商/发售日/商店链接/价格…）
- SQLite 记录提醒进度，不重复轰炸；单渠道失败不影响其他渠道
- 两种运行模式：单次检查（计划任务/crontab）与常驻监控（daemon）
- Windows / Linux 双平台

## 安装

需要 Python 3.11+。

```bash
# 建议使用虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt
# Linux
.venv/bin/pip install -r requirements.txt
```

## 快速开始

1. 复制 `config.example.yaml` 为 `config.yaml` 并填写监控对象与通知渠道（见下文）。`config.yaml` 本身不入库（含密钥与个人偏好）。
2. 先手动跑一次（首次运行即会触发窗口内所有提醒，兼作冒烟测试）：

```bash
# Windows
.venv\Scripts\python steam_monitor.py once
# Linux
.venv/bin/python steam_monitor.py once
```

3. 查看报告：`reports/` 目录下的 `report-*.md`。

## 配置说明（config.example.yaml → config.yaml）

### 监控对象

```yaml
publishers:
  # 推荐：映射形式，显式给出 creator 查询参数（见 config.example.yaml 注释说明如何获取）
  - name: 072projectx
    clan_account_id: 45479601                 # 发行商主页的 clanAccountID
    clan_announcement_gid: 509607220045941405 # 发行商主页"新发行/即将发行"tab 的 clanAnnouncementGID
  # 简化形式：只写名字，clan_account_id 自动从发行商主页解析（gid 仍需显式配置）
  - 任天堂

games:
  - 黑神话悟空       # 游戏名（自动搜索解析为 appid）
  - app/1245620/ELDEN_RING   # Steam 商店 URL（取 app/ 与 / 之间数字）
  - 1245620                  # 直接 appid
```

发行商监控使用 **creator 精准查询**（`saleaction/ajaxgetsaledynamicappquery`）：对每个发行商按其 clan 账号拉取其「即将发行 + 最新已发售」游戏列表（每发行商 1~2 次请求），不再轮询商店全局列表——快且精准。

### 检查点

```yaml
checkpoints: [+14, +7, -3]   # + = 发售前，- = 发售后；同一天跨多个检查点只发最近一个
interval_hours: 6            # daemon 模式检查间隔（小时）
cc: HK                       # Steam 商店区域代码（默认 cn；部分游戏 cn 区不可见，
                             # 发行商查询会漏掉锁区游戏，建议填实际区域如 HK/US）
cookie: "sessionid=xxxxx; steamLoginSecure=xxxxx; ..."   # 可选：登录态 Cookie
```

### 用户 Cookie（可选）

需要登录态数据（成人内容、锁区完整信息等）时填写。浏览器登录 Steam 商店后：
F12 → Network → 刷新 → 任选一个 `store.steampowered.com` 请求 → Request Headers →
复制整个 **Cookie** 行粘贴到配置。

> Cookie 是敏感凭据，`config.yaml` 已入 `.gitignore` 不会提交；过期后需重新复制。

### 代理

访问 Steam 或推送通知需要代理时配置（同时作用于两者）：

```yaml
proxy: http://127.0.0.1:7890        # http/https 同用一个
# 或分别指定：
# proxy:
#   http: http://127.0.0.1:7890
#   https: socks5h://127.0.0.1:7890
```

不配置（保持 `proxy:` 为空）则直连。

### 通知渠道

`notify.default` 为全局默认模板（可选），`notify.channels` 为渠道列表（可为空 = 仅报告文件）。
模板变量（`str.format` 语法，缺失变量渲染为空，不会报错）：

| 变量 | 含义 |
|---|---|
| `{game_name}` | 游戏名 |
| `{publisher}` | 匹配到的发行商名 |
| `{stage}` | 阶段文案（如"距发售还有 7 天"） |
| `{release_date}` | 具体发售日（YYYY-MM-DD） |
| `{release_date_raw}` | Steam 返回的日期原文 |
| `{days_until}` | 距发售天数（正=未发售，负=已发售） |
| `{store_url}` | 商店链接 |
| `{price}` | 价格文本 |

```yaml
notify:
  default:
    title: "🎮 {game_name}"
    content: "{stage}\n发售日：{release_date}\n{store_url}"
  channels:
    - provider: ntfy
      topic: my-game-topic
      priority: high
      title: "{game_name}｜{stage}"     # 渠道专属模板（覆盖全局）
    - provider: serverchanturbo
      sendkey: SCTxxx
    - provider: pushplus
      token: xxxxxxxx
```

## 通知渠道注册指南

| 渠道 | provider 值 | 注册方式 | 关键参数 |
|---|---|---|---|
| ntfy | `ntfy` | 手机装 [ntfy](https://ntfy.sh) App，订阅一个自定义 topic（如 `my-game-topic`） | `url`（服务器地址，默认 `https://ntfy.sh`）、`topic`（必填）；可选 `priority`、`token` |
| Server酱 Turbo | `serverchanturbo` | 访问 [sct.ftqq.com](https://sct.ftqq.com) 用微信扫码登录，获得 SendKey | `sendkey`（必填） |
| Server酱 V3 | `serverchan` | 访问 [sc.ftqq.com](https://sc.ftqq.com) 注册 | `key`（必填） |
| PushPlus | `pushplus` | 访问 [pushplus.plus](https://www.pushplus.plus) 微信扫码登录，获得 token | `token`（必填） |

其他 onepush 支持的渠道（bark / telegram / discord / dingtalk / lark / qmsg 等）也可直接配置，provider 值见 [onepush 文档](https://github.com/y1ndan/onepush)。

## 定时运行

### Windows 计划任务（Task Scheduler）

```powershell
# 每天 10:00 运行一次
schtasks /create /tn "Steam新游戏监控" /tr "\"E:\Git\steam新游戏监控\.venv\Scripts\python.exe\" \"E:\Git\steam新游戏监控\steam_monitor.py\" once" /sc daily /st 10:00
```

### Linux crontab

```cron
# 每天 10:00 运行一次（crontab -e）
0 10 * * * cd /path/to/steam新游戏监控 && .venv/bin/python steam_monitor.py once
```

### daemon 常驻模式

```bash
.venv\Scripts\python steam_monitor.py daemon   # 每 interval_hours 小时检查一次
```

- 进程内异常自动捕获、记录、继续，不会退出；Ctrl+C 优雅退出。
- **Linux 开机自启（systemd）**：创建 `/etc/systemd/system/steam-monitor.service`：

```ini
[Unit]
Description=Steam New Game Monitor
After=network.target

[Service]
WorkingDirectory=/path/to/steam新游戏监控
ExecStart=/path/to/steam新游戏监控/.venv/bin/python steam_monitor.py daemon
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now steam-monitor
```

- **Windows 开机自启**：把 `python steam_monitor.py daemon` 加入任务计划"登录时触发"，或使用 nssm 注册为服务。

## 其他命令

```bash
python steam_monitor.py status    # 查看当前跟踪的游戏、提醒进度与最近事件
python steam_monitor.py --config 自定义路径.yaml once
python steam_monitor.py --db 自定义.db once
```

## 工作原理（简述）

- Steam 免费 Storefront 接口（无需 Key）：`appdetails`（发售日/发行商/价格）、`saleaction/ajaxgetsaledynamicappquery`（发行商 creator 查询）、`storesearch`（名称搜索）。
- 发行商监控：对每个发行商按其 clan 账号精准拉取「即将发行 + 最新已发售」游戏列表 → 逐个查 appdetails → 新游戏出现即提醒。
- 检查点：以发售日为中心，`+N` 天前 / `-N` 天后各提醒一次，SQLite 记录进度防重复；发售日变动自动重算。
- 限流：请求间隔 1.5~2 秒，429/403 指数退避，403 停止本轮剩余请求。

详细设计见 [DESIGN.md](DESIGN.md)。

## 开发

```bash
.venv\Scripts\python -m pytest -q    # 116 个测试（全部离线，mock 网络与时钟）
```

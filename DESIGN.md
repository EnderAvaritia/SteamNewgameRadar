# Steam 新游戏监控脚本 — 设计规格

> 本文档是实现的唯一依据。实现必须逐条满足本规格，未提及的行为按"最小实现"处理。

## 1. 项目目标

监控 Steam 新游戏上线，两条监控线（同一引擎）：

1. **发行商监控**：监控指定发行商是否有新游戏发行（含"公布新游戏但无发售日"的情报跟踪）。
2. **游戏监控**：监控用户感兴趣的游戏是否临近发售 / 已发售。

## 2. 技术栈与运行环境

- Python **3.11+**（Windows + Linux 双平台）
- 第三方依赖（仅此三个）：`requests`、`PyYAML`、`onepush`（v1.9.0+，原生支持 ntfy/serverchanturbo/pushplus）
- 状态存储：标准库 `sqlite3`（单文件 DB，项目目录内）
- 配置：YAML（`config.yaml`）
- 全部 Steam 接口为免费、无需 API Key 的 Storefront 接口

## 3. CLI 形态（steam_monitor.py）

```bash
python steam_monitor.py once     # 单次检查：检查 → 提醒 → 退出（计划任务/crontab 用）
python steam_monitor.py daemon   # 常驻循环：每隔 interval 小时检查一次（默认 6h，可配置）
python steam_monitor.py status   # 查看 SQLite 中当前跟踪的游戏与已触发阶段（调试用）
```

- 两个执行模式共享同一个 `run_check()` 引擎。
- `daemon` 模式：`interval` 从配置读取，默认 6 小时；循环内异常不得导致进程退出（捕获、记录、继续）；支持 Ctrl+C 优雅退出。
- 所有控制台输出使用中文；文件读写强制 UTF-8 编码。

## 4. 配置文件 config.yaml

```yaml
# 监控对象
publishers:
  # 推荐：映射形式，显式给出 creator 查询参数
  - name: 072projectx
    clan_account_id: 45479601                  # 发行商主页的 clanAccountID
    clan_announcement_gid: 509607220045941405  # 发行商主页 tab URL 的 clanAnnouncementGID（必填）
  - 任天堂            # 简化形式：clan_account_id 自动从发行商主页解析（gid 仍需显式配置）

games:
  - 黑神话悟空       # 游戏名（自动解析为 appid）
  - app/1245620/ELDEN_RING   # Steam 商店 URL（提取中间数字）
  - 1245620                  # 直接 appid

# 检查点（天）；+ = 发售前，- = 发售后
checkpoints: [+14, +7, -3]

# 常驻模式间隔（小时）
interval_hours: 6

# 首次看到新游戏时是否通知（默认 true）。
# false = 首次看到静默入库（建立基线），只从之后的发售日公布/变更/检查点开始通知
notify_on_first_seen: true

# Steam 商店区域代码（cc 参数），影响可见游戏与价格；默认 cn。
# 部分游戏在 cn 区不可见，发行商查询会漏掉锁区游戏；建议填实际区域（如 HK / US）。
cc: HK

# 用户 Cookie（可选）：需要登录态数据（成人内容、锁区完整信息等）时填写。
# 透传为所有 Steam 请求的 Cookie 请求头；config.yaml 已 gitignore。
cookie: "sessionid=xxx; steamLoginSecure=xxx"

# 通知渠道（可为空列表 = 仅报告文件）
notify:
  # 全局默认模板（渠道未单独写模板时使用）
  default:
    title: "🎮 {game_name}"
    content: "{stage}\n发售日：{release_date}\n{store_url}"

  - provider: ntfy
    topic: my-game-topic        # provider 专属参数
    priority: high
    # title/content 可覆盖全局默认
    title: "{game_name}｜{stage}"

  - provider: serverchanturbo
    sendkey: SCTxxx

  - provider: pushplus
    token: xxxxxxxx

# 报告文件输出目录（默认 ./reports）
report_dir: reports
```

## 5. Steam 数据源（全部 keyless）

### 5.1 appdetails（核心接口）
- `GET https://store.steampowered.com/api/appdetails?appids={id}&cc=cn&l=schinese`
- 每次调用**一个 appid**（批量 CSV 已废弃）。
- 关键返回字段：
  - `data.name` — 游戏名
  - `data.steam_appid` — appid
  - `data.type` — 只跟踪 `game` 类型（跳过 dlc、demo、music 等）
  - `data.release_date.coming_soon` — bool，是否未发售
  - `data.release_date.date` — **显示字符串**（如 `"21 Aug, 2012"`、`"Q3 2026"`、`"Coming soon"`）
  - `data.publishers[]` — 发行商列表（用于发行商匹配）
  - `data.price_overview.final` / `is_free` — 价格（分为单位）
- `data` 为 null / `success` 为 false 时：跳过该 appid 并记录警告（游戏下架或被移除）。

### 5.2 发行商新游戏发现（creator 精准查询）

- 核心接口：`GET https://store.steampowered.com/saleaction/ajaxgetsaledynamicappquery`
  - 关键参数：`clanAccountID`（发行商 clan 账号）、`clanAnnouncementGID`（**必填**，缺失返回 500）、
    `flavor=all_upcoming`（只取「即将发售」栏目的未发售游戏；`flavor=all` 才会混入已发售）、
    `strFacetFilter={"type":7,"value":"game"}`（只取 game）、`start`/`count` 分页、
    `bUseCreatorHomeApps=true`
  - 响应 `appids[]` 直接返回 appid 列表；`possible_has_more` 指示是否还有下一页
- 参数来源：
  - `clanAccountID`：可显式配置，或从 `https://store.steampowered.com/publisher/{name}` 主页
    HTML 中 `data-props="{&quot;clanAccountID&quot;:...}"` 自动解析
  - `clanAnnouncementGID`：主页 **无法可靠解析**（主页 `gidEvent` 与所需 GID 相差 1，如
    404 vs 405），必须显式配置（从发行商主页"新发行/即将发行"tab 的地址栏 URL 获取）
- 流程：对每个被监控的发行商 → 按其 clan 参数拉取「即将发售」appid 列表（flavor=all_upcoming）
  → 对每个 appid 调 appdetails 补全发售日/价格 → 进入检查点流程。
- 发行商查询返回的 appid 天然属于该发行商，无需再匹配 publishers[] 字段。
- 发行商出现此前未知的 appid → 触发"新游戏公布"事件（受 §10 的 notify_on_first_seen 开关控制）。

### 5.3 游戏监控线
- 配置中的 games 解析为 appid（名称→`/api/storesearch` 搜索取第一个 `game` 类型结果；URL→提取数字；数字→直接使用）。
- 每个 appid 每天（每次 run_check）调一次 appdetails。
- 解析失败 / 模糊日期：不排检查点，但保留跟踪，下次继续重试。

### 5.4 限流（必须遵守）
- Storefront 免费接口限流约 **200 请求/5 分钟/IP**。
- 所有 Steam HTTP 请求**串行**，间隔 **1.5~2 秒**（全局请求节流器）。
- 收到 HTTP 429 / 403：指数退避（1.5s 起步 ×2，上限 60s），重试最多 3 次；403 视为被封，停止本轮剩余 Steam 请求并提示。
- 超时：连接 10s、读取 30s。所有请求设置 UA（`Mozilla/5.0` 风格）。
- 设计容量：发行商线（每发行商 1~2 次 creator 请求 + 50 个 appdetails ≈ 2 分钟）+ 关注游戏（数十个）≈ 1 次 run 在 3~4 分钟内完成，安全。

## 6. 发售日解析（防御性）

- 解析策略（按优先级）：
  1. `coming_soon == false` 且日期字符串可解析 → **已发售**，取具体日期（`datetime.date`）
  2. `coming_soon == true` 且日期字符串可解析（如 `"21 Aug, 2026"`）→ **具体发售日**
  3. 字符串含年份段如 `"Q3 2026"`、`"2026"` → **模糊日期**（`fuzzy`），保留原文
  4. 其他（`"Coming soon"`、空）→ **未知**（`unknown`），保留原文
- 支持的日期格式：`"21 Aug, 2026"`、`"Aug 21, 2026"`、`"2026-08-21"`、`"2026/8/21"`、`"21 Aug 2026"`（用 `datetime.strptime` 多个格式尝试，英文月份缩写）
- 模糊/未知日期：不排检查点；`release_date_raw` 保留原文用于通知与报告。
- 发售日解析**每天重试**：今天 unknown → 明天公布发售日 → 触发"发售日公布"事件。

## 7. 检查点提醒机制（核心）

- 检查点配置形如 `[+14, +7, -3]`：`+N` = 发售前 N 天触发，`-N` = 发售后 N 天触发。
- 每个游戏（有具体发售日的）维护状态：`(appid, release_date, last_triggered)`，`last_triggered` 为已触发的检查点序号（按配置顺序 0,1,2...）。
- 每次 run_check：
  - 以**当天日期**计算每个检查点日期 = `release_date + timedelta(days=配置值)`（注意：`+14` → `release_date - 14天`，`-3` → `release_date + 3天`）。
  - 若 `今天 >= 检查点日期` 且 `检查点序号 > last_triggered` → 触发提醒，更新 `last_triggered = 该序号`。
  - 一天内多个检查点同时跨越（如停机 3 天）→ **只发最近一个**（序号最大的），并更新状态。
- **发售日变动自适应**：本次解析出的 `release_date` 与 DB 中不同 → 视为发售日变更：
  - 触发"发售日变更"事件（旧日期 → 新日期，若新日期为具体值）
  - 重置该游戏的 `last_triggered = -1`（检查点按新日期重新计算）
- 无发售日游戏：DB 中仅记录 appid/名称/原文日期；当天公布具体发售日 → 触发"发售日公布"事件 + 正常进入检查点流程。

## 8. 事件与通知

### 8.1 事件类型（每游戏每轮最多一条通知，按优先级取最高）
| 事件 | 触发条件 | stage 文案示例 |
|---|---|---|
| `date_announced` | 从无具体日期 → 有具体日期 | `发售日公布：将于 2026-08-21 发售` |
| `date_changed` | 具体日期 A → 具体日期 B | `发售日变更：2026-09-01 → 2026-12-15（跳票）` |
| `new_announcement` | 发行商旗下出现从未见过的新游戏（无具体日期） | `新游戏公布（发售日未定）` |
| `checkpoint` | 跨越检查点 | `距发售还有 7 天` / `已发售 3 天` |

优先级（同一游戏多事件命中时取最高）：`date_announced` > `date_changed` > `checkpoint` > `new_announcement`。
（例：新游戏公布当天恰好也在某检查点窗口内，优先报"公布"。）

### 8.2 通知变量（模板渲染）
| 变量 | 值 |
|---|---|
| `{game_name}` | 游戏名 |
| `{publisher}` | 匹配到的发行商名（游戏监控线无发行商配置时为空） |
| `{stage}` | 8.1 的 stage 文案 |
| `{release_date}` | 具体日期（`YYYY-MM-DD`）；无具体日期时为空字符串 |
| `{release_date_raw}` | Steam 返回的日期原文 |
| `{days_until}` | 距发售天数（正=未发售，负=已发售，无具体日期=空） |
| `{store_url}` | `https://store.steampowered.com/app/{appid}/` |
| `{price}` | 价格文本：免费→`免费`；有价格→`¥xx`（final/100，保留最多 2 位小数）；无价格→空 |

- 模板渲染：Python `str.format` 语法；未提供的变量渲染为空字符串（不抛 KeyError）。
- 模板为 `null` / 缺省 → 使用全局 `default` 模板；全局也无 → 内置默认（`title: {game_name}`，`content: {stage}\n{store_url}`）。

### 8.3 通知投递
- 遍历 `config.notify` 中的渠道（排除 default 段），逐个调用 `onepush.notify(provider, ...)`，参数从渠道配置透传。
- **单渠道失败不影响其他渠道**：捕获异常，记入运行日志，继续下一个。
- 渠道配置合并全局默认模板（渠道写了 title/content 则覆盖全局）。
- 渠道无任何事件时跳过（不发送）。

### 8.4 报告文件（兜底，始终生成）
- 每次 run_check 生成 `{report_dir}/report-YYYY-MM-DD-HHMMSS.md`。
- 内容结构：
  - 运行时间、耗时、本轮触发的全部事件列表（按事件类型分组，含全部变量值）
  - 本轮发现但未触发通知的"新公布/发售日公布"之外的跟踪状态摘要（如：发行商旗下游戏数量、无日期游戏列表）
  - 错误/警告摘要（限流、解析失败、下架等）
- 报告文件保留最近 30 份，旧的自动清理。

## 9. SQLite 状态（state.db）

```sql
CREATE TABLE games (
    appid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    publishers TEXT,              -- JSON 数组
    release_date TEXT,            -- ISO 日期或 NULL
    release_date_raw TEXT,        -- 原文
    release_status TEXT,          -- 'released' | 'scheduled' | 'fuzzy' | 'unknown'
    source TEXT,                  -- 'publisher' | 'game'
    publisher_match TEXT,         -- 命中的发行商名（publisher 线）
    last_triggered INTEGER,       -- -1 未触发，0..n 已触发的检查点序号
    last_seen TEXT                -- 上次见到该游戏的时间（ISO datetime）
);

CREATE TABLE events_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appid INTEGER, event_type TEXT, stage TEXT, created_at TEXT
);
```

- 首次出现（publisher 线）→ INSERT + 触发 `new_announcement`（若同时有具体日期则直接进检查点流程）。
- 已存在 → UPDATE 字段 + 比较发售日变化。
- `release_status` 为 `released` 且 `今天 - release_date > 30 天` 的游戏可从 `games` 表删除（归档到 `events_log` 不删），避免表膨胀。
- 名称解析失败的游戏单独记日志，不进 DB。

## 10. 首次运行行为

- 首次运行对窗口内检查点/事件正常触发（兼作冒烟测试）。
- 「新游戏公布」事件受配置 `notify_on_first_seen` 控制：
  - `true`（默认）：首次看到的发行商游戏立即通知"新游戏公布"（首次运行会较吵）。
  - `false`：首次看到静默入库（建立基线），不产生"新游戏公布"事件；发售日公布/变更/检查点照常。

## 11. 模块划分（steam_monitor/ 包）

```
steam_monitor.py                 # CLI 入口（argparse 子命令 once/daemon/status）
steam_monitor/
  __init__.py
  config.py                      # YAML 加载 + 校验 + 默认值合并
  steam_api.py                   # Steam 客户端：请求节流器、appdetails、search/results、storesearch、重试/退避
  resolver.py                    # 游戏名/URL/appid → appid；发行商列表抓取
  date_parser.py                 # 发售日防御性解析
  state.py                       # SQLite 存取
  checkpoints.py                 # 检查点计算与触发判定
  events.py                      # 事件类型定义与优先级
  notifier.py                    # 模板渲染 + onepush 投递 + 报告文件生成
  engine.py                      # run_check 编排
```

- 每个模块职责单一，禁止 `as any` 式类型逃逸（Python 侧：禁止裸 `except:`，异常必须记录上下文）。
- 网络层全部集中在 `steam_api.py`（便于测试 mock）。

## 12. 测试要求（pytest）

- 单元测试（**不访问真实网络**，全部 mock `requests` / 注入假客户端）：
  1. `date_parser`：各日期格式、模糊/未知、coming_soon 组合
  2. `checkpoints`：检查点跨越多日只触发最近一个、发售日变更重置、`+/-` 符号语义
  3. `state`：INSERT/UPDATE、发售日变更、归档清理
  4. `resolver`：名称/URL/appid 三种输入、发行商匹配（大小写/空白）
  5. `notifier`：模板渲染（缺变量不抛错、默认模板回退）、渠道失败隔离、报告文件生成
  6. `engine`：全链路集成（mock Steam 响应）：发行商新游戏出现 → 事件；无日期跟踪 → 公布日期 → date_announced；首轮触发窗口内检查点
  7. 限流器：请求间隔、429 退避、403 停止
- 测试夹具（fixtures）存放假 appdetails / search 响应 JSON。
- 测试不得依赖真实网络或真实时钟（时间用可注入的 `today()`）。

## 13. 交付物

1. `steam_monitor.py` + `steam_monitor/` 包（含全部模块）
2. `tests/`（pytest 套件）
3. `config.yaml`（模板，含注释）
4. `requirements.txt`
5. `README.md`（安装、配置、Windows 计划任务、Linux crontab、daemon 开机自启、通知渠道注册指南）
6. `DESIGN.md`（本文档）

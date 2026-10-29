# 控制台 API 契约 v1（冻结）

> 适用：`console/server.py`（服务端）与 `console/ui/`（前端）。
> 状态：**v1 冻结**。前端只能依赖本文件里的字段；服务端新增字段必须追加，不得改名/删除。
> 设计文档：`docs/25-跨环境控制台.md`；ADR：`decisions/015-控制台解耦为独立本地只读服务.md`。

---

## §0 三条不变式（任何实现都不得违反）

1. **只读**：控制台只读数据目录，**不写**任何 profile 数据、不改 DSH 配置。HTTP 只有 `GET/HEAD`，其余方法一律 `405`。
2. **脱敏在服务端**：原始对话文本、记忆 `content`/`sourceQuote`、注入预览、审计里的 `preview`/`quote`/`argsHead` **不允许出现在任何响应里**。前端拿不到原文，也就无从泄漏。字段白名单见 §4。
3. **旧 profile 未接入 ≠ 报错**：目标数据目录不存在时返回 `available:false`，带 `reason` 与 `hint`；**不得**伪造 0 值或拿别的 profile 的数据顶替。

---

## §1 传输与安全

- 默认监听 `127.0.0.1:3000`，可用 `--port` 改端口；**拒绝**绑定非回环地址（除非显式 `--allow-remote`，本设计不推荐）。
- 无 CORS 头（同源页面）。`Host` 头**精确匹配**回环：`127.0.0.1:<port>` / `localhost:<port>` / `[::1]:<port>`
  （端口必须等于监听端口；无端口形式也接受）。带 `@`、尾随串、错误端口一律 `421`。
- 响应头固定：`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、
  `X-Frame-Options: DENY`、
  `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'`。
  `Server` 头只含 `LingshuConsole/<version>`，**不暴露 Python 版本**。
- `HEAD` 与 `GET` 同头但**绝不返回 body**（HTTP/1.1 协议要求）。
- 响应体统一 UTF-8 JSON；时间戳一律毫秒整数 `ts`（非法值收敛为 `0`），另给 `iso`（`Asia/Shanghai`，带偏移）。
- 错误体：`{"ok":false,"code":"<enum>","message":"<固定错误码文案>","hint":"<可选固定提示>"}`。
  **`message`/`hint` 是固定文案，不得包含数据文件原值或异常原文**（否则等于给脱敏开旁路）；
  服务端异常细节只写 stderr，不回显。
  错误码：`bad_request`(400) / `unknown_profile`(404) / `not_found`(404) / `method_not_allowed`(405) / `bad_host`(421) / `too_many_streams`(503) / `internal`(500)。

---

## §2 端点

### §2.1 `GET /api/health`

```json
{"ok":true,"service":"lingshu-console","version":"1.1.0","uptime_s":42,
 "read_only":true,"bind":"127.0.0.1:3000","profile_count":4,"streams":0}
```

### §2.2 `GET /api/profiles`

```json
{"ok":true,
 "generated_at":1793017508836,
 "repo":"<REPO_ROOT>",
 "dsh_home":"<DSH_HOME>",
 "registry":"<REPO_ROOT>\\console\\profiles.json",
 "privacy":{"policy":"allowlist-v1","raw_text":false,
            "omitted_fields":["content","sourceQuote","source_quote","quote","preview",
                              "argsHead","subject","object","evidenceQuote","text"]},
 "profiles":[
  {"id":"desktop","label":"日常 desktop（静默监控）","kind":"runtime","mode":"observe",
   "color":"#38bdf8","available":false,"reason":"data_dir_missing",
   "hint":"先运行 scripts/console_desktop_onboard.ps1（人工确认后才会改 desktop profile）",
   "source":{"type":"jsonl","resolved_dir":null,"display":"~/.dsh/plugins/dsh-lingshu/profiles/desktop"},
   "stale_after_sec":180,"last_write_ts":null,"data_age_sec":null,"stale":true,
   "files":{"state.json":{"exists":false,"bytes":0,"mtime":null},
            "memory.jsonl":{"exists":false,"bytes":0,"mtime":null},
            "audit.jsonl":{"exists":false,"bytes":0,"mtime":null}},
   "warnings":[]}
 ]}
```

- 路径展示（`display`/`resolved_dir`）**必须脱敏**：用户家目录替换为 `~`，仓库根替换为 `<repo>`，DSH_HOME 替换为 `~/.dsh`。
- `display` 在数据目录尚未出现时是**计划路径**（便于用户知道去哪里接入）；`resolved_dir` 只在真正存在时非 null。
- `warnings` 是字符串枚举：`source_unavailable` / `legacy_flat_dir` / `unparsable_lines` / `count_truncated` /
  `state_missing` / `threshold_default` / `source_fallback`。
- `/api/profiles` 顶层另含 `thresholds{shift,hard}`、`threshold_source`（`rules/decay-params.yaml` 或 `default`）、
  `registry_warnings`（注册表级告警，如 `local_override:profiles.local.json`）。

### §2.3 `GET /api/snapshot?profile=<id>`

`profile` 必填；未知 → `404 unknown_profile`。

```json
{"ok":true,
 "profile":{ "...": "同 /api/profiles 的元素" },
 "generated_at":1793017508836,
 "revision":"9f2c1a…",
 "privacy":{"policy":"allowlist-v1","raw_text":false,"omitted_fields":["content","sourceQuote"]},
 "freshness":{"data_asof":1793017508095,"data_age_sec":7,"stale":false,
              "sources":[{"file":"state.json","mtime":1793017508095,"bytes":2006}]},
 "warnings":[],
 "turns":{"count":8,"state_writes":2,"sessions":0,"stage":"cold"},
 "memory":{"observed":true,"total_rows":120,"unique":118,"active":100,"superseded":15,
           "suppressed":5,"unparsable":0,"truncated":false,
           "by_origin":[{"key":"goal","n":60}],"by_type":[{"key":"procedural","n":50}],
           "by_layer":[{"key":"warm","n":80}],
           "by_status":[{"key":"active","n":100}],
           "last_write_ts":1793017508095},
 "phase":{"observed":true,"ratio":0.7231,"phase":"shift","trend":0.02,
          "anchor_required":false,"prefeed":true,
          "context_window":1048576,"used_tokens_est":758000,
          "thresholds":{"shift":0.7,"hard":0.85},
          "alerts":2,"last_alert_at":1793017000000,"source":"plugin_state"},
 "stpa":{"observed":true,"verdict":"allow","ucas":[],"downgrades":[],"blocked":false,
         "fail_mode":"closed","sources":{"user_message":false,"injected_prompt":false},
         "last_at":1793017508095,"mode":"observe"},
 "audit":{"observed":true,"total":164,"unparsable":0,"errors":0,"truncated":false,
          "phase_alerts":2,"last_alert_ts":1793017000000,
          "by_kind":[{"kind":"pre_step","n":51}],
          "recent":[{"ts":1793017508095,"iso":"2026-10-27T19:58:28.095+08:00","kind":"pre_step",
                     "fields":{"stage":"cold","matched":0,"level":"unknown","verdict":"allow",
                               "ucas":[],"downgrades":[],"blocked":false,"ratio":0.0,"phase":"normal"}}],
          "events_limit":30},
 "versions":{"param_version":null,"prompt_version":null,"compose_version":null}}
```

**规则**

- `memory.observed:false` 表示 memory 文件不存在；`phase.observed:false` 表示 state 里没有 `control.phase`。
  **不得**写成 `ratio:0` 冒充"正常"；前端必须显示"未观测"。
- `memory.total_rows` 是物理行数；`unique` 是 `id` last-wins 后的条目数；`active/superseded/suppressed` 按最后一条的 `status` 计。
- 文件超过读取上限时 `truncated:true` 并加 `count_truncated` warning（当前上限：memory 20 万行 / audit 20 万行 / 单次 16 MB）。
- `revision` 由 `(memory.unique, turns.count, phase.ratio, phase.phase, audit.total, stpa.verdict)` 等内容哈希得到；前端用它判断是否需要重绘。
- `phase.source` 取 `plugin_state`（读 `state.control.phase`）或 `derived`（observe 模式由插件累计消息字符估算，见 docs/25 §5）。
- 阈值来自 `rules/decay-params.yaml#control`（`phaseShiftRatio` / `phaseShiftHardRatio`），**不在前端硬编码**。

### §2.4 `GET /api/events?profile=<id>&limit=100`

近期事件（已脱敏、已反转，最新在前）。`limit` 1..200，默认 100。

```json
{"ok":true,"profile_id":"desktop","events":[
  {"ts":1793017508095,"iso":"…","kind":"pre_step","fields":{"verdict":"allow","ucas":[]}}
]}
```

### §2.5 `GET /api/stream?profile=<id>`

`text/event-stream`。连接后立即推一帧，之后：

- 内容变化时推 `event: snapshot`，`data:` 是 §2.3 的完整 JSON（单行）。
- 无变化时每 15s 推一行注释心跳 `: ping`。
- 客户端断开即释放线程；**同一 profile 最多 8 个并发流**（超限 `503 too_many_streams`）。
- `?once=1`：推一帧后关闭（测试用）。
- 切换 profile = 前端关闭旧 EventSource、用新 `profile` 重连；**服务端无状态、不重启**。

### §2.6 静态页面

- `GET /` → `console/ui/index.html`；`GET /app.js`、`/app.css`、`/favicon.svg` 映射到 `console/ui/` 同名文件。
- 只允许白名单文件名；`..`、绝对路径、URL 编码绕过一律 `404`。

---

## §3 Profile 注册表（`console/profiles.json`）

```jsonc
{
  "version": 1,
  "default_profile": "desktop",
  "refresh_hint_ms": 1500,
  "privacy": {"policy": "allowlist-v1", "raw_text": false},
  "profiles": [
    {
      "id": "desktop",                        // ^[a-z0-9][a-z0-9_-]{0,31}$
      "label": "日常 desktop（静默监控）",
      "kind": "runtime",                      // runtime | lab | demo
      "mode": "observe",                      // observe | active（仅用于 UI 徽章）
      "color": "#38bdf8",
      "stale_after_sec": 180,
      "source": {
        "type": "jsonl",                      // jsonl | sqlite
        "dirs": ["${DSH_HOME}/plugins/dsh-lingshu/profiles/desktop"]   // 取第一个存在的；也可用 "dir": "…"
      }
    }
  ]
}
```

- 变量展开：`${DSH_HOME}`、`${USERPROFILE}`（= 家目录）、`${REPO}`（仓库根）、`${HOME}`。
- `source.type="sqlite"`：`{"type":"sqlite","path":"${REPO}/.console-data/x.db"}`，
  表契约见 docs/25 §6；当前实现为**实验性**，表缺失 → `available:false, reason:"sqlite_schema_missing"`。
- 注册表可由 `console/profiles.local.json` 覆盖（git 忽略，现场配置）；合并规则：按 `id` 覆盖，新 id 追加。
- 注册表损坏 → 服务启动失败并给出可执行提示（不是静默空列表）。

---

## §4 脱敏白名单（服务端唯一实现位置：`console/redact.py`）

**记忆记录**：只输出聚合计数（§2.3）。单条记忆**不输出**。禁止键：`content` `sourceQuote` `source_quote`
`subject` `object` `evidenceQuote` `quote` `quoteHead` `preview` `argsHead` `text` `message` `arguments` `args`。

**审计事件**：按 `kind` 白名单，白名单外字段丢弃并置 `fields_omitted:true`。允许的标量只包括：

```
turn step firstStep stage level matched excluded chars phase ratio anchor conflict fallback
dropDomains verdict ucas downgrades blocked failMode fail_mode written origins reason_code
sections toolOk frozenOk shadow saturated thresholds alerts count n ids_count type
goalEvents relationEvent trust modifier source kinds
```

特殊规则：

| kind | 允许字段 | 说明 |
|---|---|---|
| `pre_step` | stage matched level excluded turn step firstStep chars phase ratio anchor conflict fallback dropDomains verdict ucas downgrades blocked | 无内容 |
| `stpa_observe` / `preflight_block` | verdict ucas downgrades sources(dict[str,bool\|来源枚举]) turn | `sources` 只保留布尔/来源类别 |
| `preflight_downgrade` | downgrades | |
| `memory_write` | written origins ids_count | `rejected` 丢弃（含 content） |
| `memory_supersede` / `memory_conflict` | count | 不输出 subject/objects |
| `memory_cleanup` | reason ids_count | |
| `quality_label` | kind_label | `quote` 丢弃 |
| `fact_extract_rejected` | reason | |
| `phase_alert` | phase ratio thresholds | |
| `tool_gate` | tool verdict ucas reason_code | `argsHead`/`argsDigest` 丢弃 |
| `agent_state` | goalEvents relationEvent trust conflict modifier | `preview` 丢弃 |
| `session_event` | type count | |
| `apply` | sections toolOk frozenOk shadow | |
| `hook_error` / `section_error` | — | 只留 kind/ts |

> 前端**不得**尝试读取 `fields` 之外的内容；服务端也不提供"查看原文"开关。

---

## §5 前端行为要求（A3 交付）

1. 首屏拉 `/api/profiles` 填下拉框；默认选 `default_profile`，目录不可用时显示空态与 `hint`（引导脚本），**不自动切到别的 profile**。
2. 每次切换 profile：关闭旧 EventSource → `GET /api/snapshot?profile=新id` → 重连 `/api/stream?profile=新id`；**整页不刷新**。
3. 相变环形图：`ratio` 0→1 映射弧长；颜色 `normal` 绿 `#22c55e`、`shift` 琥珀 `#f59e0b`、`critical` 红 `#ef4444`、未观测灰；
   70%/85% 处画刻度线；中心显示百分比、phase 名；下方显示 `used_tokens_est / context_window`。
4. 记忆卡片：显示 `active` 与 `unique`，并以**客户端内存**保留最近 60 帧计数画 sparkline（服务端不存历史，不得伪造历史）。
5. STPA 日志：展示 `stpa` 摘要 + `audit.recent` 中 verdict/ucas/downgrades 事件；只显示事件类型与枚举字段。
6. 陈旧/未接入/告警：`stale:true` 显示"数据陈旧 Ns"；`available:false` 显示接入提示；`warnings` 逐条显示。
7. 隐私徽章：固定显示"只读 · 不展示对话原文"。
8. 无外网请求（无 CDN/字体/图片外链）；支持 1024px 与 1440px 宽；空态可读。

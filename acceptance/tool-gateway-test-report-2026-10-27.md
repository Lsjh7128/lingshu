# 工具网关（Actuator STPA Pre-Gate）测试通过报告

- **日期**：2026-10-27
- **契约**：`docs/24-工具网关UCA.md` v1.1（`policy_version: t-uca-1.1`，16 条 T-UCA：13 block / 3 degrade，无 allow 规则）
- **ADR**：`decisions/013-解冻行动闭环-工具网关.md`（H0 显式越权解冻，仅限本网关）
- **环境**：Windows · Python 3.12.14（捆绑 runtime）· Node v24 · DSH danger-full-access
- **判定**：**增量通过**。新增套件全绿；既有套件无「由本次改动引入」的失败（2 项失败为环境/并发产物漂移，见 §5）。

---

## 1. 交付物

| 类型 | 文件 |
|---|---|
| 网关（引擎） | `engine/tool_gateway.py`（纯函数、零依赖、fail-closed；16 条规则） |
| 网关（插件） | `plugin/dsh-lingshu/tool-gateway.js`（同构 + `guardTool()` 包装器） |
| 规则表 | `docs/24-工具网关UCA.md` §7（唯一来源，JSON 机器可读）+ 同步器 `tools/_sync_tool_policy.py` |
| 测试 | `tests/test_tool_gateway.py`（79 checks）、`plugin/dsh-lingshu/tool-gateway-sim.mjs`（65 assertions） |
| 参考循环 | `tools/run_actuator_demo.py` → `paper/ablation/results/actuator-demo-20261027.json` |
| 插件接线 | `plugin/dsh-lingshu/index.js`（`tool/call` / `tool/result` 会话事件）+ `host-sim.mjs` 新增 22 条断言（既有 188 条未改） |
| 留出集 | `paper/ablation/tool/heldout-tool-calls-v1.jsonl`（盲写 10 条，sha256 `f68fc1b3…`） |
| 论文 | `paper/sections/06e-actuator.tex`（886 词）、`10c-appendix-tool.tex`、`paper/DRAFT.md` |

## 2. 新增测试矩阵

| 套件 | 断言/检查 | 结果 |
|---|---|---|
| `tests/test_tool_gateway.py` | **79 checks** | **0 failed，exit 0** |
| ├ 危险场景（构造） | **20/20 拦截 = 100%**，覆盖全部 16 条 T-UCA | PASS |
| ├ 良性场景 | **0/8 误杀** | PASS |
| ├ Python↔JS parity | 20 case：`verdict` / `ucas` / `degraded_to` **各 20/20 一致** | PASS |
| └ 契约 parity | docs/24 §7 == Python POLICY == JS POLICY（16 条逐字段） | PASS |
| `plugin/dsh-lingshu/tool-gateway-sim.mjs --assert` | **65 assertions**（含 `guardTool` 真前置阻断 4 条） | **0 failed，exit 0** |
| `plugin/dsh-lingshu/host-sim.mjs --assert` | **210 passed / 0 failed / 0 skipped**（既有 188 条未改 + 新增 22 条） | PASS |
| `control-sim.mjs` / `mutation-regressions.mjs` / `stress.mjs` | 148 / 15 / 40 轮压测 | 全绿（exit 0） |

## 3. 参考循环（真前置阻断）证据

`tools/run_actuator_demo.py` → `actuator-demo-20261027.json`：

- 12 次脚本化 tool 调用（mock 执行器，**model_calls=0**、**零真实写**）；
- **allow 4 / block 8 / degrade 0**；被拦截的 8 条**均未执行**（`executed=4` 仅对应 allow）；
- 命中 **9 条不同 T-UCA**（01/02/03/04/05/06/10/12/15）；
- 盲写留出集 10 条：**仅 args 口径 10/10 被拦；带 context_hint 口径 10/10 被拦**（只报网关判定，**不报"拦对率"**，人工裁定未做）。

## 4. 红线核对

| 红线 | 结果 |
|---|---|
| 不改记忆逻辑 / 情感 PAD | `engine/memory.py`（10-26 18:51）、`engine/emotion.py`（10-25 20:17）、`plugin/memorywriter.js`（10-26 23:34）**mtime 均早于本次改动**，未触碰 |
| 不改注入侧 UCA-01..08 | `engine/control.py`（10-26 19:29）未改；T-UCA 独立命名空间，两闸并列 |
| 不改既有断言通过标准 | `host-sim` 既有 188 条一字未改（总数 188→210 只增不改）；其余套件断言未改 |
| 不新增能力 | 未改 `rules/capabilities.yaml`、`plugin/cordis.patch.yml`；未注册任何写工具 |
| 只做增量 | 新增 6 文件 + `index.js` 独立分支；回滚方式见 ADR-013 §5 |

## 5. 既有套件回归（诚实版）

改动前基线（2026-10-27 06:14，同一环境）：

- Python 15 文件：**14 exit 0**；`test_run_chaos.py` 失败 1 条（`真实 host S2: beta_hit=true`——最新 host 产物 `memory_conflict.beta_hit=false`，属既有产物漂移，与本次改动无关）。
- JS 4 套件：全绿。

改动后（06:30）：

- Python：**13 exit 0 + 新增测试 exit 0**；`test_run_chaos.py` 仍失败同 1 条；另外 `test_chaos_campaign.py` 失败 1 条（`自测后 .last.json 不指向已删除的 selfcheck run`）。
  - 归因：`acceptance/chaos/v2/.last.json` 与报告在 **06:17–06:29** 被工作区内的并发写入者反复更新（该文件 mtime 06:29:21，晚于本测试运行），与本增量无调用关系（网关不写 `acceptance/chaos/v2/`；基线 06:14 时该套件为绿）。
  - 结论：**不计入本次增量失败**，但需在并行工作流停止后复跑确认。
- JS：5 套件全绿（含新 `tool-gateway-sim.mjs`）。

## 6. 边界（必须与数字同读）

1. **"100%"是构造场景的拦截率，不是防御率**：20 条危险场景由设计者构造，留出集只有 10 条。
2. **宿主无 pre-tool dispatch 钩子**：插件对任意宿主工具只能事后检测 + 下一轮强提示 + 审计（`blocking_capable=false` 为硬字段）；真前置阻断只在 ①引擎参考循环 ②`guardTool()` 包装过的工具。
3. **绕过面**：不经网关的宿主工具/其他插件路径，网关无效。
4. **误杀未标定**：degrade 需人工确认，长会话 critical 相位下属高风险误杀面（T-UCA-10/16）。
5. **规则可被编码绕过**：网关只做静态解析，不做沙箱执行；ReDoS/payload 深度上限已设，但未做真实红队。

## 7. 复现命令

```powershell
$py = "<PYTHON>"
$node = "<NODE>"
$env:PYTHONIOENCODING = "utf-8"

& $py tools\_sync_tool_policy.py --check     # docs/24 §7 与两处实现一致
& $py tests\test_tool_gateway.py             # 79 checks, exit 0
& $node plugin\dsh-lingshu\tool-gateway-sim.mjs --assert
& $py tools\run_actuator_demo.py             # 生成 actuator-demo JSON
& $py tools\verify_tool_chain.py             # Lead 独立验收（含全量回归）
```

---

*本报告的数字均来自 2026-10-27 06:14–06:30 的实跑；契约版本 `t-uca-1.1`。与 `docs/24` 冲突时以 `docs/24` 为准。*

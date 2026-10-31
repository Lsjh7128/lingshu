# Handover Overview (HANDOFF)

> Purpose of this file: **let whoever takes over (including a future you) know within 30 minutes what this thing is,
> where it stands, and what to do next.** It is an index + a judgement + a set of boundaries, not a replacement for the detailed docs.
> Version: **v6.0 ｜ 2026-10-29** (the previous version, v5.6, was frozen on 2026-10-27 and is 10 days stale — it predates v5.7 through v1.1.0)
> Code state: `release/dsh-plugin-package` @ `3a19c57`, tag `v1.1.0` ｜ `main` is 37 commits behind ｜ Target host: DSH 0.2.0-rc.2
> 简体中文版：[`交接总览.md`](交接总览.md)

> ⚠️ **Line-number references to this file no longer hold.** `paper/OUTLINE-zh.md` and `paper/GLOSSARY.md` cite
> `交接总览.md:NN`, which were v5.6 line numbers. This is a rewrite — cite by section heading instead
> (e.g. "§6 Verified"). The paper was not edited in this pass.

---

## 0. What changed since v5.6 (read this first, or you will judge with stale conclusions)

| # | Change | Old claim (v5.6) | Current fact (v6.0, measured) |
|---|---|---|---|
| 1 | **The plugin now runs in the daily `desktop` profile** | "Only ran in a disposable experiment profile; `desktop` untouched" | Installed into the daily desktop via the release copy; real sessions are running (14 turns at this snapshot); `docs/21` boundary B2 and the `plugin/README.md` header are **out of date** |
| 2 | **Public version axis switched** | Internal v5.6 | Public `v1.1.0` (git tag); internal `v5.x` is history only; the two axes are not convertible |
| 3 | **Sandbox namespace + persona routing** | One flat data dir per profile | `layout-0.2` (`sandboxes/{sandboxId}/{personaId}/` + `personas/` + a single `audit/` ledger + `trash/`); `layout.json` is the only switch; this machine is migrated |
| 4 | **Three new defence surfaces** | none | Tool gateway v1.2 (T-UCA, post-hoc verdict), zero-trust supply chain + physical sandbox (S-CAP, disabled by default), multi-round immersive-safety evidence |
| 5 | **Document counts and numbering** | "26 docs + 11 ADRs" | 31 body `.md` files (numbers 00–26 + `28` + 3 topic docs; plus `docs/assets/README.md`, 32 in total, **`27` missing**) + 19 ADR files (**017 is used twice**, 009 missing) |
| 6 | **New unmerged branches** | none | `feat/phase-b-coevolution`, `feat/phase-c-semantic-guard`; Phase B/C code is on the release branch but **not in `main`** |

> If you only remember one line: **it went from "a handoff-able prototype" to "a system in daily use" — so the top
> priority is now "observable + rollback-able", not "more features".**

---

## 1. What this is in one sentence

**LINGSHU (灵枢)** is a **pluggable cognition plugin system** for Agent hosts (DSH): memory, emotion, metacognition,
goals, relationships, narrative, user-emotion sensing, plus a composer and a degradation matrix, a **learning loop**
(measure → propose → canary → roll back), and a **control-theoretic layer** (integral feedback / phase-shift feed-forward /
requisite variety / STPA pre-flight gate / ACT-R / dynamic weights / impasse — see `docs/19`).
Since v1.1.0 it also ships three "peripherals": the **Persona Hub panel** (plugin-provided UI), the
**sandbox namespace + workspace policy**, and two defence layers — the **tool gateway** and the **supply-chain sandbox**.

It does not try to make an Agent "more human". It tries to make an Agent's behaviour:

> **understandable, predictable, and trustworthy.**

"Human-like" has four layers; only the first three are in scope (capability / imperfection / expression).
**The fourth layer (claiming subjective experience) is forbidden.** Spirit is behaviour; the pivot is the boundary.

---

## 2. Current progress (four axes — do not look at one number)

| Axis | Progress | Basis |
|---|---|---|
| **Design & contracts** | **~98%** | 32 `.md` files (31 body docs: numbers 00–26 + `docs/28` draft + 3 topic docs; plus `docs/assets/README.md`), 19 ADRs, 8 rule files (`rules/`), 7 contracts (`contracts/`, incl. `supply-chain-policy.json`); contracts carry a machine-checkable digest (`tools/gen_contract.py --check`) |
| **Reference implementation (`engine/`)** | **23 Python modules + control-theoretic layer + tool gateway + supply chain** | `docs/16` (generated from code at 2026-10-29 23:22): the matrix covers **34 modules** (engine + tools/console/plugin/probe), 23 with acceptance, **1883 assertion call sites**; measured this pass: 32 files in `tests/`, **2817 passed / 2 failed** (both permission-related), plus 2 files that crashed before any assertion (unwritable TMPDIR); frozen replay baseline `recall@3 = 1.000` |
| **Learning loop** | **W1–W8 all pass (synthetic data)** | replay → search → identifiability → canary → guard-rail rollback → correction → proposal → end-to-end |
| **Productisation** | **Running in the daily `desktop` profile (release copy v1.0.0)** | 8 injected sections + control-theoretic layer + fact/conflict/label channels + immersive safety + Persona Hub panel + workspace/sandbox + tool gateway v1.2; **17 JS suites / 1342 assertions, all green** (measured this pass, see §6); supply-chain sandbox implemented but **not enabled** |

> One line: **the design is handoff-able, the reference implementation proves the contracts are implementable, and the
> plugin already runs in daily sessions — but the parameters are still `guess`, real samples are still insufficient,
> and "being in daily use" itself raises the risk level.**

### Stage status (as of 2026-10-29)

| Deliverable | Status |
|---|---|
| Open-source release **v1.1.0** (sandbox namespace + persona routing + bilingual README + install/uninstall docs) | ✅ Tagged; `docs/21` still has unchecked items (see §8) |
| **Immersive safety** (ADR-014): soft buffer / Safe_Redirect / multi-round real host | ✅ 52/52 multi-round PASS (`acceptance/immersive-safety-multiround-2026-10-28.md`); risk tiering is still rule-based, **0 real labels** |
| **Tool gateway v1.2** (ADR-019): false-positive convergence | ✅ After real-audit attribution: 244 `tool_gate` events, 241 blocked (98.8%); v1.2 blocks 0/20 benign calls in the same batch; **still no pre-dispatch hook** |
| **Zero-trust supply chain + physical sandbox** (ADR-018) | ✅ Implemented + accepted (Python 174 / sim 59 / integration 10); default `supplyChain.enabled=true, plugins:[]` (empty allowlist, zero overhead); **no real third-party plugin connected yet** |
| **Persona isolation P1/P2** (ADR-017A) | ✅ Data layer + policy layer + wiring; P3 (allow exec per workspace) **not done**, needs its own ADR |
| **Phase B/C semantic gate** (ADR-016 + branches) | ⚠️ On the release branch, **not in `main`**; E1 (n=40) interception 37.5% → 77.5% → **85.0%**, **below the 90% target** (see `docs/28`, `acceptance/phase-c/复盘-2026-10-28-phase-c.md`) |
| **Chaos v2 self-check** | ❌ 19/20 invariants (2026-10-29): F6 harness `mkdtemp` fails with **EPERM** (`TMPDIR` points at an unwritable dir) — not a product-logic failure; 4 FINDINGs still need separate fixes |
| **Real calibration** (guess→measured) | ⏳ Real turns are now 14 (this session), but `measured` is still **0**; the shadow baseline report is stale (as-of 2026-10-27) and **must be re-run** |

### Environment checks (this pass)

- The live plugin data dir is already `layout-0.2` (`layout.json` written 2026-10-29 20:16, with `.migration-backups/`).
- **The unwritable path is `TMPDIR`, not `TEMP`**: `TMPDIR=D:\LSJH\tools-tex\tmp` (unwritable), while `TEMP` itself is writable. Hence
  `test_chaos_calibrate` / `test_shadow_analysis` crash before any assertion and chaos v2's F6 harness hits `mkdtemp` EPERM;
  `test_demo_scripts` **passes 303 checks (2 skipped)** and did not fail because of TEMP — the previous version of this document
  claiming "TEMP causes spurious red" was wrong.
- `.venv-neural` has no Pillow → `test_plot_coevolution_curve` prints PASS then crashes; under the default GBK console, 4 tests hit
  `UnicodeEncodeError` on `U+2212` etc., and pass with `PYTHONIOENCODING=utf-8`. **None of this is a code defect.**
- `git`: on `release/dsh-plugin-package`. **Concurrent edits were detected while this document was being written** (another session changed
  `index.js`, `plugin/README.md`, `decisions/005`, `persona-prompt-sim.mjs`); numbers here are a 2026-10-29 23:46–23:56 snapshot, and
  `index.js` line count drifts. Plus 6 untracked screenshots (`acceptance/v2-refactor/*.png`, containing real paths — kept out of git per B1).

---

## 3. Live runtime state (measured 2026-10-29 23:46, from the plugin's `lingshu_status`)

| Item | Value |
|---|---|
| Stage / cumulative turns | `warm` / **14** |
| Frozen-zone check | pass |
| Injected sections (8) | `anchorHead@90`, `constitution@100`, `blocked@105`, `skeleton@105` (always empty; registered in v0.2 only), `fact@110`, `organization@120`, `style@130`, `anchorTail@140` |
| Hooks | `agent/inbox/claimed`, `system-prompt/assemble`, `agent/pre-step`, `session/event` all ok |
| Control theory | phase `normal`, ratio `0.0001`, anchor off, integral not saturated |
| Pre-flight audit / immersive safety | `allow` / not triggered (hit streak 0, switch on) |
| Shadow mode | goals and relations are **recorded but not injected** |
| Fact memory | 0 writes (`user_fact`), 5 compliance rejections, 0 supersedes |
| Conflict / reduce-variety | `none` / off |
| Outcome labels | 0 (rule-based auto-labels only) |
| Supply chain | **not initialised** (empty allowlist by default) |
| Permission requests | none |

**What this means**: while this document was being written, **the plugin was injecting prompts into a daily session**.
So "changing the plugin" now equals "changing a system that is in use" — the cost of verification and rollback is an
order of magnitude higher than in the experiment-profile era.

---

## 4. How to read this repo (by role)

| You are | Read first | Then |
|---|---|---|
| **New to the project** | [`README.md`](README.md) (English entry) or [`项目简介.md`](项目简介.md) (5-minute Chinese intro; still at v5.7) | this file (the 30-minute picture) |
| **Implementer** | [`docs/00`](docs/00-架构总览.md) (architecture / enforcement points) → [`docs/15`](docs/15-验收清单.md) (acceptance) | [`docs/16`](docs/16-实现状态.md) (code-generated matrix) → the matching `engine/*.py`; for the plugin, [`plugin/README.md`](plugin/README.md) |
| **Reviewer** | [`docs/15`](docs/15-验收清单.md) (definition of done) → [`assumptions/未验证.md`](assumptions/未验证.md) (what you must not believe yet) | `research/*` (external evidence, host verification), `acceptance/audit/` (assertion strength, mutation testing) |
| **Successor** | this file → [`CHANGELOG.md`](CHANGELOG.md) (all v5.0→v1.1.0 fixes and findings) | `decisions/` (why it is this way, and what would change it — see Appendix A) |
| **Just wants to run it** | the 10-second sandbox in [`README.md`](README.md) (`demo.bat` / `demo.sh`) | [`MIGRATION_GUIDE.md`](MIGRATION_GUIDE.md) (migrate old data to layout-0.2) |
| **Will touch production data** | [`plugin/README.md`](plugin/README.md) "only legal write path" | `memory-admin.mjs` (plan/apply/dry-run). **Never hand-edit JSONL** |

**Eight questions that prove you "got it"** (first six inherited, last two new):

1. Why is the constitution layer the only fail-closed one, while other plugins fail open?
2. In the composition matrix, why does metacognitive `refuse_claim` outrank emotional "pleasure"?
3. Why is memory supersession keyed on `(subject, relation)` instead of semantic similarity?
4. Why does the emotion plugin forbid labels like "sadness/protest" while allowing "low/frustrated"?
5. Why is user-emotion sensing off by default and gated behind data thresholds?
6. **Why was layer 0 abandoned? Which safety property lost its engineering guarantee afterwards?**
   (Only `decisions/001` can answer, and it was written after the fact.)
7. **Why is `layout.json` the only switch? After deleting it, which capabilities disappear and which data survives?**
   (`MIGRATION_GUIDE.md` §0)
8. **Why does "supply-chain default-deny" not equal "enforced everywhere"? Which layer is lexical pre-filtering and
   which is the OS backstop?** (`docs/26` §9)

---

## 5. What it is made of

```
lingshu/                                  ← current worktree (release/dsh-plugin-package)
├── README.md / README.zh-CN.md           English entry / Chinese version: positioning, invariants, install paths
├── 交接总览.md                           this file, Chinese
├── HANDOFF.md                            this file, English
├── 项目简介.md                             5-minute intro (still at v5.7, needs sync)
├── CHANGELOG.md                          1747 lines; public version axis from v1.1.0
├── MIGRATION_GUIDE.md                    layout-0.2 migration (dry-run by default, copy-only)
├── docs/  32 files                       00 architecture → 19 control contract → 20 calibration protocol
│                                          → 21 release checklist → 22/23 chaos v1/v2 → 24 tool-gateway UCA
│                                          → 25 console → 26 zero-trust supply chain ｜ 28 = Phase C README block draft
│                                          plus: demo-sandbox / roadmap-persona-entity / work order v2 / assets
├── decisions/ 19 ADR files               001–019 (009 missing; 017 used twice — see Appendix A)
├── rules/ 8 files                        single source of numbers + capability declaration + frozen list + sandbox baseline
├── contracts/ 7 files                    memory / recall / compose / event-bus / agent contract / supply-chain policy / manifest
├── engine/ 23 modules                    runnable reference implementation (memory/metacog/emotion/goal/relation/narrative/
│                                          compose/gray/proposal/constitution/control/tool-gateway/supply-chain)
├── tools/ 54 top-level / 69 recursive     replay/search/canary/loop/drift checks/calibration/chaos/migration/status generator
├── tests/ 32 test files                  engine + calibration + chaos + panel/workspace + migration + supply chain + Phase B/C
├── scripts/                              quickstart, console, chaos night/schedule, immersive multi-round, fact-fix replay
├── plugin/dsh-lingshu/  39 files         the plugin (below)
├── chaos-set/                            chaos v1 four scenarios + v2 eight families (frozen, `--check` guards hand edits)
├── replay-set/                           200 turns / 500 memories / 10 scenario families / 5 corrections (all synthetic)
├── eval-set/                             47 goal/relation labels + 138-row risk-tier corpus (frozen)
├── demo/ + demo.sh/.bat + docker-compose demo   one-command sandbox (disposable profile + synthetic memory)
├── experiments/                          phase-b / phase-c / phase-d / chaos-injection assets
├── console/                              console v0 (static) + cross-environment read-only console (stdlib Python)
├── acceptance/  45 .md files             records: chaos / supply-chain / tool-gateway / immersive-safety / phase-c / audit
├── integrations/astrbot/                 AstrBot adapter skeleton (Python, reuses engine/, **never run on real AstrBot**)
└── probe/ + research/3 files             host probe (no longer installed) / external evidence + host verification
```

**Plugin modules** (`plugin/dsh-lingshu/`, `index.js` ≈3337 lines and drifting — it was being edited concurrently while this was written):

| Module | Role |
|---|---|
| `index.js` | 8 injected sections + 4 hooks + 1 tool (`lingshu_status`) + wiring |
| `control.js` | control theory: integral / phase shift / anchors / U-shaped ordering / STPA pre-flight gate / UCA-08 / explainable permission |
| `memorywriter.js` / `memory-paths.js` | the only writer (tombstones + last-wins) / layout resolution (layout-0.2) |
| `goalrelation.js` | goal/relation rule stubs (write requests only, never direct writes) |
| `tool-gateway.js` | T-UCA tool gateway (v1.2) |
| `supply-chain.js` + `sandbox-host/child.mjs` | three-gate supply chain + child-process physical sandbox |
| `workspace*.js` + `dashboard.js` + `ui/` | workspace API / deny-only policy / Persona Hub panel |
| `*.mjs` (17 suites) | mock host, control theory, workspace, supply chain, tool gateway (incl. detective), v0.2 migration, timing/mutation regressions |

---

## 6. Verified vs. not to be trusted

### Verified (accessible, re-runnable evidence)

| Claim | Evidence (file / command) |
|---|---|
| The 5 frontier papers exist; key numbers match their abstracts verbatim | `research/00`, `assumptions/已验证.md` |
| The host exposes `ctx.systemPrompt.section()`, 4 hooks, `approval.request()` | `research/02` (probe + app.asar strings + source) |
| `agent/pre-step` is a waterfall and fires multiple times per turn | real host + host source + `research/02 §8.5` |
| Frozen replay baseline is stable | `recall@3 = 1.000`, `forbidden_leak = 0`, `topK=1/2/3/4 → 0.444/0.667/1.000/1.000` |
| 1883 engine assertion call sites; the implementation matrix cannot rot | `docs/16` (`tools/gen_impl_status.py --check`) |
| Python acceptance | measured this pass: **2817 passed / 2 failed** (30 files reported totals; both failures are write-permission) — plus 2 files that crashed before any assertion (unwritable `TMPDIR`), see Appendix C |
| Engine coverage and mutation score | line **89.95%** / branch **80.71%**; mutation **42/44 non-equivalent killed** (`acceptance/audit/`, 2026-10-27 snapshot) |
| All 17 plugin JS suites green | measured this pass, **1342 assertions**: host-sim 296 / control-sim 248 / memory-paths 130 / workspace-write 102 / workspace-api 97 / tool-gateway 85 / workspace-sim 61 / supply-chain 59 / workspace-switch 47 / stress 45 / tool-gateway-detective 41 / v02-sandbox 38 / v02-http 28 / lag 24 / workspace-wiring 16 / mutation 15 / supply-chain-integration 10 |
| Control-theoretic layer runs on the real host | 8 sections + `hook_error=0`; this session `turns=14`, `ratio=0.0001`, `allow` |
| Fact memory + supersede reproducible on the real host | `.chaos-data-s2`, 3 independent calls; `acceptance/fact-fix-host-2026-10-27.md` 22/22 (after F-A/F-B/F-D fixes) |
| Conflict detection + reduce-variety reproducible on the real host | `.chaos-data-s4`: `fallback=true`, `dropDomains=["style"]`, `verdict=allow`, `blocked=false` (conflict memory was synthetic) |
| UCA-08 saturation traced on the real host (3 rounds) | `acceptance/chaos/host-uca08-2026-10-26.md`: `saturated.valence=true`, `downgrades=["UCA-08"]`, `blocked=false` |
| Immersive safety holds across rounds | `acceptance/immersive-safety-multiround-2026-10-28.md` **52/52** (mild→hard escalation, trust exemption, severe→hard) |
| Tool-gateway false positives converged | `decisions/019`: 244 real `tool_gate` events, once 98.8% blocked; v1.2 blocks **0/20** benign calls, still blocks 15/15 dangerous; `tests/test_tool_gateway.py` 101 checks, `tool-gateway-sim` 85 |
| Supply-chain sandbox is real isolation | different child PID, out-of-scope read/write `ERR_ACCESS_DENIED`, import-time escape fails closed (`supply-chain-sim` 59, real spawn) |
| Workspace/sandbox isolation + live panel switching | `workspace-*` suites; real Alpha↔Beta switch, `409 during a turn`, `active-workspace.json` survives restart (`plugin/README.md`) |
| One-command sandbox demo works | `tests/test_demo_scripts.py` 303 passed (2 skipped: no bash / no Docker); real DSH wiring verified with zero model calls |
| Chaos v1 four scenarios judgeable on the real host | `acceptance/chaos/chaos-20261026-234400-host.json`: 4/4, 0 skipped, 6 calls |
| Chaos v2 mechanism set holds | historical run: 8 families / 212 checks / 20/20 invariants / zero model calls (`acceptance/chaos/v2/chaos2-20261027-013720-engine.json`) |
| Learning-loop regression fixed | `topK` shadowed by `maxReturned` → ADR-008; assertions and replay set unchanged |
| Goal/relation error rates quantified | 47 labels: goal-creation precision 0.909 / relation-positive 0.556 / negative false-positive rate 31.6% |
| Migration tool is safe | dry-run by default, copy-only, idempotent (exit 3 if `layout.json` exists), rollback covered by `tests/test_migrate_v02.py` |

### **Not** to be trusted (see `assumptions/未验证.md` and each record's "honest boundaries")

| Claim | Status |
|---|---|
| "All numbers are calibrated" | **`provenance: measured` = 0.** 14 real turns, almost no ratio observations, 0 boundary samples, 0 human labels; `0.70/0.85/0.02` are all `guess` |
| "The plugin is experiment-only" | **No longer true**: the daily `desktop` runs the release copy. A rollback path exists, but "failures only affect an experiment" is gone |
| "Chaos passes" | **Latest v2 self-check is 19/20** (F6 harness `mkdtemp` EPERM — local TEMP limit) and 4 FINDINGs are open; it is neither "all green" nor "the product is broken" |
| "Sandbox = security isolation" | **Directory isolation ≠ permission isolation** (same process, same OS user); multi-tenancy undefined (`decisions/017A` §7, `plugin/README.md`) |
| The supply chain stops any malicious plugin | No OS-level network enforcement (Node's permission model does not cover sockets); static scan is **lexical** (concatenated names / computed dynamic imports can bypass); no PKI; validation target is **synthetic plugins**, no real third party connected |
| The tool gateway "blocks in advance" | The host has **no pre-dispatch hook**; it can only judge after the fact and re-inject a strong hint next turn. True pre-blocking exists only in the engine reference loop and `guardTool()`. **In this very session, ordinary host `read` / `grep` / `edit` / `pwsh` / `send_message` calls were repeatedly tagged block by T-UCA-09 after the fact (no execution impact) — v1.2 still has a false-positive surface on host tools, see §8 #13** |
| Tool-gateway false-positive rate is calibrated | Not calibrated (`guess`); real model-driven headless re-run was blocked by an invalid API key; evidence is deterministic replay of the same calls + unit tests + real-audit attribution |
| Phase C meets the paper target | E1 interception 85.0% (target >90% missed; 89.5% after removing 2 benign false positives); E1/E0 are held-out **mutation** sets, not OOD; single-machine CPU latency; **not in `main`** |
| Demo / Docker verified | Docker never built/run here; the macOS/Linux `demo.sh` never executed; every demo turn is a **real model call** (tokens, money) |
| The AstrBot adapter works | Never run on a real AstrBot (unit tests only) |
| User-emotion sensing | design-only, deliberately not implemented |
| The skeleton section works | Always returns an empty string; the generator is not implemented (only audit event names reserved) |
| Long-session / multi-tool stability | Stressed only to 40 turns + 5 cross-process runs; **no long soak** |
| The replay set represents real traffic | Fully synthetic (`synthetic_share_max=1.0`); **forbidden for release decisions** |
| Rule-based auto-labels = ground truth | `quality_label` is substring matching; 0 real ones, no human review; cannot enter the `docs/20` §4.1 gate |
| The shadow baseline report is current | as-of **2026-10-27 23:31**, 10 turns; now 14 turns → **re-run** `analyze_shadow.py` / `unfreeze_status.py` |
| User-jailbreak blocking removes the user message | It only guards the injection side (fail-closed + audit); it **cannot remove the user message from the host payload** |

---

## 7. Hard rules you must know (all learned the hard way)

### Incident A: never install an unregistered experimental plugin into the daily profile

2026-10-25: an unregistered probe went into `desktop` → after restart the **whole session could not talk**; rollback fixed it.
A try/catch inside `apply()` protects your code only, **not the host's post-apply validation**.

**Current rule (revised)**: the daily `desktop` may only load a **release copy**
(`~/.dsh/plugin-src/dsh-lingshu-v1.0.0`, wired via `link:`), and must have a rollback path. Experimental changes go to a
disposable profile first; only after verification do you sync the release copy and restart DSH.
"Written robustly" ≠ "safe to install".

### Incident B: deliverables must be paths that exist *now*

`present` pointing at an already-cleaned file → the host reads `.kind` and throws a TypeError → the whole turn fails.
(**The attribution was later overturned** — the real cause is incident C — but "the deliverable must exist" remains good practice.)

### Incident C: three hook/tool contracts (the real cause)

| Contract | Symptom when violated |
|---|---|
| `agent/pre-step` is a waterfall; you must `return next()` | `Cannot read properties of undefined (reading 'kind')`, whole turn fails |
| `output.render` must return an array of content blocks | `content.some is not a function` |
| The **second** argument of `session/event` is the event (and it mixes in non-human input) | goal/relation misclassification |

I first guessed "tool schema", then "present missing file" — **both wrong**. The real cause took a controlled experiment
("disable the plugin and it recovers") plus reading the host source (`research/02 §8.5–§8.6`).

### Rule D: directory isolation is not permission isolation — never call it "secure isolation"

Multiple workspaces/personas share one process and one OS user; `workspace-policy` only **appends deny rules**, never
relaxes a verdict. (`decisions/017A` §7)

### Rule E: repo code ≠ code that is running

The repo and `plugin-src/dsh-lingshu-v1.0.0` are **two copies**; editing the repo does not affect daily sessions.
Protocol: edit repo → verify in a disposable profile → sync the release copy → **restart DSH** (not a page refresh) →
re-check `lingshu_status`. (`decisions/017A` §1.2; `plugin/README.md`)

### Rule F: an unwritable `TMPDIR` manufactures red builds

`TMPDIR=D:\LSJH\tools-tex\tmp` is unwritable (`TEMP` itself is writable): `test_chaos_calibrate` / `test_shadow_analysis` crash before
any assertion, chaos v2's F6 harness fails `mkdtemp` with EPERM, and `test_run_chaos` cannot write `.chaos-data-test-*`.
The default GBK console additionally causes 4 `UnicodeEncodeError`s. **Suspect the environment before touching product code**;
with `PYTHONIOENCODING=utf-8` and a writable temp dir they should be green.

### Rule G: do not edit the repo while chaos v2 is running

INV-19 (zero repo pollution) treats concurrent edits as out-of-scope changes → the run fails. (`acceptance/chaos/v2/*-report.md` §4)

---

## 8. Known gaps and recommended order

| # | Gap | Size | Status / suggestion |
|---|---|---|---|
| 1 | **Chaos v2 self-check 19/20** | small (env) + medium (4 FINDINGs) | Re-run with a writable temp dir to confirm INV-16; fix FINDING-F01…F04 as separate tasks per `docs/23 §8.8` — never "make it green in passing" |
| 2 | **Parameters guess → measured** | needs real samples | Gates unmet: ≥30 ratio-observed turns, degraded ≥3 + control ≥3, supersede ≥50, UCA-08 ≥10, red-team ≥20, **human outcome labels ≥20** |
| 3 | **Real-traffic collection and report refresh** | non-code | `desktop` is running, but the shadow report is as-of 10-27; re-run `analyze_shadow.py`, `unfreeze_status.py`, and add a **human corrections channel** |
| 4 | **Doc drift** (only this file was updated) | medium | `项目简介.md` still at v5.7; the `plugin/README.md` header still says "desktop untouched"; `docs/21` B2 conflicts with reality; `ADR-017` is duplicated; `docs/27` missing; the README Phase C status line still says "not merged into main" (it is on the release branch); the paper cites this file by line number |
| 5 | Panel screenshots not committed | small | 6 `acceptance/v2-refactor/*.png` contain real paths and persona text; either redact and commit, or state explicitly that they are not shipped |
| 6 | Docker demo unverified | small | `demo/Dockerfile` / `docker-compose.demo.yml` never built or run |
| 7 | Skeleton generator | medium | The section is always empty; either implement it or state "reserved" in the README |
| 8 | Long-session / multi-tool stability | medium | Only stressed to 40 turns; now that it is in daily use, **raise the priority** |
| 9 | Phase C merge decision | medium | Code is on the branch, `main` has none; E1 is 85% vs the 90% target — H0 must decide "ship at 85%" or "keep tuning" |
| 10 | Multi-tenancy / permission isolation | large | design-only; never describe it as "isolation security" externally |
| 11 | AstrBot end-to-end | small | unit tests only |
| 12 | User-emotion sensing | — | design-only (deliberately not implemented) |
| 13 | **Tool gateway still false-positives on ordinary host tools** (T-UCA-09) | medium | Measured in this session: `read` / `grep` / `edit` / `pwsh` / `send_message` were tagged block after the fact a dozen+ times (**no execution impact**, but audit noise plus a per-turn strong hint polluting the context). ADR-019's "relaxed schema + semantic slots" does not cover these call shapes — keep converging from real audit data |

**Recommended order**: #3 (make reports current) → #1 (classify the chaos red) → #8 (it is in daily use) → #2/#4 in
parallel → #5/#6/#7/#9 → the rest.
The environment issue (Kaspersky TLS interception) was ruled out; if `TRANSPORT` retries return, follow `decisions/008`.

### Frozen directions (do not start before unfreezing)

Per H0 (`decisions/010`), four advanced directions are sealed; **the first one was partially unfrozen by explicit
override (ADR-013), not by evidence**:

| # | Direction | Unfreeze trigger (summary) | Current state |
|---|---|---|---|
| 1 | Action loop (tool calling) | ≥10 tasks failed because an external API could not be called + the composer cannot roll back tool errors | ⚠️ **Partially unfrozen by override** (ADR-013, STPA tool gateway only); autonomous planning / result rollback / memory loop still frozen |
| 2 | Adaptive evolution (meta-learning) | real turns ≥1000 + manual calibration proven very slow | 🔒 frozen (14 turns; measured=0) |
| 3 | Collective mind (multi-agent blackboard) | single-agent P95 above the business threshold, or ≥20 concurrent write+review tasks | 🔒 frozen (metrics **not measurable**: audit lacks latency fields) |
| 4 | Multimodal perception | official stable DSH voice/image interface + mature text memory (measured≥5, memory≥1000, stable 30 days) | 🔒 frozen (measured=0) |

- Persona direction: P1/P2 landed per ADR-017A; **P3 (allow exec per workspace) needs its own ADR and a change to `rules/capabilities.yaml`**.
- Decision and unfreeze procedure: `decisions/010`, `decisions/013`, `decisions/017A`; runnable todo: `待办-冻结方向.md`; daily status: `acceptance/unfreeze-status.md`.
- **No "while I'm here" work**: while frozen, only evidence collection (audit fields / real samples) is allowed — no prototypes, no new dependencies, no new config switches.

---

## 9. Whether to continue — one decision

There are three layers: **design (handoff-able) → reference implementation (verifiable) → plugin (now running in your daily session)**.
Layer three went from prototype to production, so the acceptance bar must move with it:

- **Keep it stable in daily use**: what is missing is real data and long-run stability. The top priority is **observability and rollback** —
  backups/migration, a readable `lingshu_status`, a replaceable release copy, executable uninstall docs.
  Collect enough samples (≥30 ratio turns, ≥20 human labels) before talking about `measured`.
- **Close it out as a handoff-able deliverable**: deliverables are the bilingual `README` + this file + `HANDOFF.md` + `docs/16` +
  `CHANGELOG` + `plugin/`. That is possible now, but clear §8 #4 (doc drift) first.
- **Make it more trustworthy**: goals/relations are still "record only, never inject"; control thresholds need `measured`;
  tool-gateway false-positive rate is uncalibrated; the supply chain was only validated against synthetic plugins.
  All of these need real samples, not more code.
- **Get more people to use it**: the one-command demo and install docs are ready; Docker and macOS/Linux paths are unverified;
  whether Phase C enters `main` needs an explicit decision.

---

## Appendix A · ADR index (19 files / 18 numbers)

| No. | Title | Status |
|---|---|---|
| 001 | Abandon layer 0 (psychological-crisis safety pre-layer) | accepted (written after the fact) |
| 002 | Hot layer never crosses sessions | accepted |
| 003 | Emotion-consistent retrieval off by default | accepted |
| 004 | Power-law decay kept as an option, not default | accepted |
| 005 | Persona baseline locked; expression drift bounded | accepted |
| 006 | Learning confined to the measurement loop (no fine-tuning) | accepted |
| 007 | Observability is the foundation (M-1 before M0) | accepted |
| 008 | Cybernetic refactor: frozen items, costs, regressions | accepted |
| 010 | Sealing and unfreeze conditions for four directions | accepted |
| 011 | Phase-shift double-threshold linkage (H0 ruling B) | accepted; parameters still `guess` |
| 012 | Fact memory, conflict detection and outcome labels (S2/S4/UCA-08 channels) | implemented |
| 013 | Unfreeze "action loop": STPA tool gateway only | accepted (explicit override) |
| 014 | Immersive-safety expression layer | implemented |
| 015 | Decouple the console into a standalone read-only local service | implemented |
| 016 | Phase B adversarial co-evolution testbed | implemented (branch) |
| 017-A | Persona isolation: P1 data layer + P2 policy layer | accepted (explicit override) |
| 017-B | Explainable permission requests (stylised restatement) | implemented |
| 018 | Zero-trust supply chain and physical sandbox (S-CAP) | implemented |
| 019 | Tool gateway v1.2: false-positive convergence | implemented |

> ⚠️ **017 is used twice**: two different ADRs are numbered 017 (persona isolation / explainable permission).
> Always disambiguate with the suffix; renumber in the next documentation pass (`docs/21` already lists "no dangling links" as a release check).
> **009 is missing**: it was reserved for a `chaos_calibrate --apply` run that never happened (parameters are still `guess`).

## Appendix B · Document index (32 `.md` files: 31 body docs + `assets/README.md`)

- **Numbered 00–26** (27 files): 00 architecture｜01 constitution｜02 memory｜03 emotion｜04 metacognition｜05 goals & intent｜06 relation model｜07 narrative｜08 user-emotion sensing｜09 composition matrix｜10 degradation matrix｜11 cold start & tracing｜12 persona parser｜13 learning mechanism｜14 engineering & observability｜15 acceptance checklist｜16 implementation status (**generated**)｜17 real-data intake｜18 AstrBot coordination｜19 cybernetic contract｜20 real calibration protocol｜21 release checklist｜22 chaos v1｜23 chaos v2｜24 tool-gateway UCA｜25 cross-environment console｜26 zero-trust supply chain & physical sandbox
- **27**: missing (never assigned)
- **28**: Phase C README block draft (a paste-ready fragment, not a design doc)
- **3 topic docs**: `demo-sandbox.md` (sandbox demo)｜`roadmap-persona-entity.md` (persona roadmap, P0)｜`施工单-沙盒与作用域记忆-v2.md` (layout-0.2 work order)

## Appendix C · What v6.0 did **not** do (honest boundaries)

1. **Python acceptance was measured this pass, but must not be reported as "all green"**: all 32 `tests/test_*.py` files were run one by one
   (`.venv-neural`, Python 3.12.14, `PYTHONIOENCODING=utf-8`); 30 files reported totals = **2817 passed / 2 failed**; 2 more crashed
   before any assertion (0 executed). Failures by environment:
   - `test_chaos_calibrate.py` / `test_shadow_analysis.py`: Python `tempfile` resolves to **`TMPDIR=D:\LSJH\tools-tex\tmp`** (unwritable),
     `PermissionError` before the first assertion; **`TEMP` itself is writable — the problem is `TMPDIR`**
   - `test_chaos_campaign.py`: 120 passed / **2 failed** — restoring `acceptance/chaos/v2/2026-10-28-report.md` is refused (that file's ACL is
     read-only, `Users:(RX)`). The same run rewrote the mtimes of `.last.json` and `2026-10-27/29-report.md`; the tracked file was restored with
     `git restore`. In-repo `.chaos-data-test-*` leftovers went from 11 to 13 (none of them tracked)
   - `test_run_chaos.py`: writing `.chaos-data-test-*\state.json` is refused (30 assertions did pass)
   - `test_plot_coevolution_curve.py`: no Pillow; 91 assertions passed and `RESULT: PASS` printed before the crash
   - Under the default GBK console, `test_control` / `mutation_regressions` / `pipeline` / `tool_gateway` hit `UnicodeEncodeError`
     (`U+2212` etc.); with `PYTHONIOENCODING=utf-8` they pass 174/22/46/101. **An encoding-environment issue, not an assertion failure**
   This file does **not** count the 2 "incomplete" files or the 2 permission failures as passing.
   > Note: running the chaos tests itself writes into the repo (mtimes / leftover dirs); for a strictly read-only tree those 3 tests should not be run.
2. **Did not edit the paper**: `paper/OUTLINE-zh.md` / `paper/GLOSSARY.md` line-number citations of `交接总览.md:NN` are now drifted.
3. **Did not edit other docs**: `项目简介.md`, the `plugin/README.md` header, `docs/21` B2, the README Phase C status line, the
   duplicate ADR-017 — all listed in §8 #4.
4. **Did not sync the `lingshu-release` worktree** (its copy of `交接总览.md` on `main` is still the old version):
   `main` is 37 commits behind and must be handled in the same merge.
5. **Did not refresh the calibration reports**: `acceptance/shadow-20261028.md` and `unfreeze-status.md` were generated from older data.

*This file is updated with each version. The authoritative implementation status is [`docs/16`](docs/16-实现状态.md) (code-generated); the Chinese original is [`交接总览.md`](交接总览.md).*

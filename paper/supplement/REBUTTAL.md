# Rebuttal Templates — Engineering Validation

> **用途**：审稿人质疑"测试是否真实/严密"时直接用。每条都自带报告里的数字，**不得脱离数字单独使用**。
> **唯一数据源**：`acceptance/audit/` 三份审计报告 + 两份 JSON（2026-10-27）。引用时给到 `acceptance/audit/` 的路径。
> **通用纪律**：① 只写"回归检出能力"，不写"防御效果"；② 出现 100% 时必须紧跟 "of non-equivalent mutants"；③ 主动认限制（无 git 历史、单点变异、无跨语言对拍），比被动被问更好。

---

## R1 — "Assertion counts are not evidence of test rigor."

**Use when** a reviewer argues that 892 + 396 assertions may be superficial, padded, or tautological.

**Draft**
> We agree that counts prove nothing, which is why we audited the suite rather than
> reporting it. A fixed-seed sample of 238 assertions (165 Python, 73 JavaScript)
> contained zero tautologies, and a full heuristic scan of 1,192 static assertions
> found exactly one tautology, which we fixed. We also rewrote two
> oracle-self-referential assertions whose expected values were read from the
> implementation's own unsafe-control-action table. We report the residual
> weakness rather than hiding it: 227 weak assertions in Python (27.6% weak or
> tautological overall), and 42/42 non-equivalent mutants killed after
> supplementation. Details: Appendix [A], Table A2(c).

**Must cite**: 238 sample / 179 strong / 59 weak / 0 tautologies; 1 tautology of 1,192; 26.6% weak-or-tautological; 227 weak (Python); 42/42 non-equivalent.

**Do not say**: "all 892 assertions are independent precise literals" (false); "no tautologies" without the full-scan number.

---

## R2 — "45.81% coverage is low for an engineering-reliability claim."

**Use when** a reviewer quotes the whole-repository Python number as evidence of thin testing.

**Draft**
> The 45.81% figure is the whole repository (47 files) and it is dominated by
> `tools/`, where only 3 of 26 files execute (32.92%). The component this paper
> makes claims about is covered far more densely: `engine/` is at 89.95% line /
> 80.71% branch, and `control.py` — the module that implements every control-plane
> mechanism — has no unexecuted line (141/141 lines, 56/56 branches). We also do
> not use coverage to claim the absence of dead code: two equivalent mutants
> survive precisely because a branch is unreachable, which is exactly the
> distinction a coverage number cannot make. `tools/` coverage is a lower bound,
> since subprocess-invoked tools are recorded as unexecuted. Table A2(a).

**Must cite**: 45.81/43.17 (47 files); engine 89.95/80.71; control.py 141/141 & 56/56; tools 32.92, 3/26; equivalent mutants as coverage ≠ dead-code proof; lower-bound caveat.

**Do not say**: "coverage proves there is no dead code"; "branch coverage is exact" (Python fallthrough side is approximated; JS block coverage is an approximation).

---

## R3 — "44 mutants is a small study, and two survived."

**Use when** a reviewer questions mutation-testing scale or the two survivors.

**Draft**
> The study is deliberately scoped: 44 single-fault semantic mutants over the two
> implementations of the control plane (29 JavaScript, 11 Python) and the symbolic
> rule file (4), each run against the complete suites (1,249 assertions per round,
> roughly 55,000 assertion executions). The baseline killed 38/44 (86.4%); after
> adding regression assertions for four genuine blind spots it kills 42/44
> (95.5%). The two survivors are equivalent mutants: the negative branch of the
> integral clamp is unreachable under the invariant `integral >= 0` that holds for
> the contract's positive gain. Our claim is therefore "42/42 non-equivalent
> mutants killed", not "100% detection". A third party independently re-ran 14
> mutants on a separate copy and killed 14/14. We acknowledge single-fault-only
> scope; Table A1.

**Must cite**: 44 / 29 JS / 11 Py / 4 rules; 1,249 assertions/round; 38/44 = 86.4%; 42/44 = 95.5%; 42/42 non-equivalent; 14/14 independent.

**Do not say**: "44 mutants suffice" as a general claim; "100% killed" without "non-equivalent".

---

## R4 — "The authors audited themselves; and without version history this cannot be trusted."

**Use when** a reviewer questions independence, or notices that no git history exists.

**Draft**
> The audit was built to be adversarial and reproducible. Mutation testing was run
> by one reviewer role and assertion/coverage analysis by a second; a subset was
> independently re-run by a third party on a separate repository copy (14/14
> mutants killed, consistent with the original matrix). All mutants were applied to
> a full copy, and the 30 implementation source files are hash-identical before and
> after (checked = 30, changed = 0): only test files changed. We state the evidence
> boundary plainly rather than overclaiming — this repository has no version-control
> history, so we can certify the current suite but cannot prove that no assertion was
> weakened at an earlier time.

**Must cite**: role separation; separate copy; 30 files SHA-256, changed = 0; only tests changed; 14/14; **no git history** limitation.

**Do not say**: "we can prove the tests were never weakened"; "audit is fully independent" (roles are within one project, even if separate agents).

---

## R5 — "Regression tests do not show the gate actually stops jailbreaks."

**Use when** a reviewer conflates the engineering audit with an effectiveness claim.

**Draft**
> Correct, and we do not claim otherwise. The audit measures whether the suite can
> detect semantic regressions in the gate's logic, not whether the gate improves
> real-world outcomes. What we can show is narrower and checkable: 42/42
> non-equivalent mutants of the gate and memory-write paths are killed; four real
> blind spots (two in in-batch supersession, one defensive filter in conflict
> detection, one per-word pinning of the quality-label vocabulary) were found and
> closed, verified by "original passes / mutant fails". Effect claims remain
> bounded by Section 7: all numeric parameters are guesses (`measured = 0`), real
> traffic is eight turns, and held-out jailbreak evaluation is reported separately
> as rule-level detection. Appendix [A]; Section 7.

**Must cite**: 42/42 non-equivalent; the four blind spots; original-pass/mutant-fail verification; measured = 0; eight turns; rule-level scope.

**Do not say**: "100% defense"; "the gate prevents jailbreaks"; anything that upgrades rule-level detection into behavioural effectiveness.

---

## 附：一句话英文骨架（当字数受限时）

- Rigor: "We audited the suite, not just its size: 238 sampled assertions with zero tautologies, one tautology found in a full scan of 1,192 and fixed, and 42/42 non-equivalent mutants killed."
- Coverage: "Engine line/branch coverage is 89.95%/80.71% and `control.py` has no unexecuted line; the 45.81% whole-repository figure is a `tools/` artefact."
- Independence: "Mutants ran on a copy, the 30 implementation files are hash-identical (changed = 0), and a third party re-ran 14 mutants: 14/14 killed."
- Boundary: "We claim relapse detection, not defensive effectiveness; parameters remain guesses (measured = 0)."

// risk_classifier_parity.mjs —— 风险分级器跨语言一致性探针（node 端）
//
// 用途：`tools/eval_risk_classifier.py` 用它跑插件侧实现，
// 与 `engine/control.py#classify_user_risk` 的输出逐条比对（ADR-014 §3 契约）。
//
// 协议（stdin/stdout JSON，UTF-8）：
//   输入：{"items": [{"id": "rc-xxx", "text": "...", "trust_level": "low|mid|high"}, ...]}
//   输出：{"ok": true, "source": "plugin/dsh-lingshu/control.js",
//          "results": [{"id", "tier", "pattern", "harmless_joke", "trust_exempt", "emotion"}, ...]}
//   失败：{"ok": false, "error": "..."}，退出码 1。
//
// 为什么单独放一个小工具而不是在 Python 里嵌 JS：插件实现是 ESM、零依赖，
// 用 node 原样 import 才能保证被测的是**真正给宿主跑的那份代码**，不是复制品。
// 本工具只读、无 IO、不写任何文件、不调模型。

import { classifyUserRisk } from '../plugin/dsh-lingshu/control.js';

const SOURCE = 'plugin/dsh-lingshu/control.js';

function fail(message) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(message) }) + '\n');
  process.exitCode = 1;
}

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => chunks.push(c));
    process.stdin.on('end', () => resolve(chunks.join('')));
    process.stdin.on('error', reject);
  });
}

async function main() {
  let raw;
  try {
    raw = await readStdin();
  } catch (err) {
    fail('读取 stdin 失败: ' + err);
    return;
  }
  // 去掉可能的 BOM（Windows 下重定向容易带 BOM，JSON.parse 会因此失败）。
  if (raw.charCodeAt(0) === 0xfeff) raw = raw.slice(1);
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    fail('stdin 不是合法 JSON: ' + err);
    return;
  }
  const items = payload && Array.isArray(payload.items) ? payload.items : null;
  if (!items) {
    fail('输入必须是 {"items": [...]}');
    return;
  }
  const results = items.map((item) => {
    const id = item && item.id !== undefined ? String(item.id) : '';
    const text = item && item.text !== undefined ? String(item.text) : '';
    const trust = item && item.trust_level !== undefined ? item.trust_level : 'low';
    const r = classifyUserRisk(text, trust) || {};
    return {
      id,
      tier: r.tier === 'mild' || r.tier === 'severe' ? r.tier : 'none',
      pattern: r.pattern === undefined ? null : r.pattern,
      harmless_joke: Boolean(r.harmless_joke),
      trust_exempt: Boolean(r.trust_exempt),
      emotion: r.emotion === undefined ? null : r.emotion,
    };
  });
  process.stdout.write(JSON.stringify({ ok: true, source: SOURCE, results }) + '\n');
}

main().catch((err) => fail('未捕获异常: ' + (err && err.stack ? err.stack : err)));

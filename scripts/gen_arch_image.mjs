// 调用豆包（火山方舟 ARK）Seedream 生图能力，生成灵枢架构图
// 用法: node scripts/gen_arch_image.mjs --prompt-file <path> --out <png> [--size 2K] [--model ...]
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const args = process.argv.slice(2);
function arg(name, def = null) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
}

const cfgPath = arg('config', path.join(os.homedir(), '.dsh', 'volc_ark.json'));
const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
const model = arg('model', cfg.model);
const size = arg('size', '2K');
const out = arg('out');
const promptFile = arg('prompt-file');
if (!out || !promptFile) {
  console.error('缺少 --prompt-file 或 --out');
  process.exit(2);
}
const prompt = fs.readFileSync(promptFile, 'utf8');
fs.mkdirSync(path.dirname(out), { recursive: true });

const body = {
  model,
  prompt,
  size,
  response_format: 'b64_json',
  watermark: false,
};

const url = `${cfg.endpoint}/images/generations`;
console.error(`[gen] POST ${url} model=${model} size=${size}`);
const t0 = Date.now();
const resp = await fetch(url, {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${cfg.api_key}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify(body),
});
const text = await resp.text();
if (!resp.ok) {
  console.error(`[gen] HTTP ${resp.status}\n${text.slice(0, 2000)}`);
  process.exit(1);
}
let json;
try { json = JSON.parse(text); } catch {
  console.error('[gen] 响应不是 JSON:\n' + text.slice(0, 2000));
  process.exit(1);
}
const item = json?.data?.[0];
if (!item) {
  console.error('[gen] 响应无 data[0]:\n' + text.slice(0, 2000));
  process.exit(1);
}
let buf;
if (item.b64_json) {
  buf = Buffer.from(item.b64_json, 'base64');
} else if (item.url) {
  const r2 = await fetch(item.url);
  if (!r2.ok) { console.error(`[gen] 下载失败 HTTP ${r2.status}`); process.exit(1); }
  buf = Buffer.from(await r2.arrayBuffer());
} else {
  console.error('[gen] data[0] 无 b64_json/url:\n' + JSON.stringify(item).slice(0, 1000));
  process.exit(1);
}
fs.writeFileSync(out, buf);
const usage = json.usage ? JSON.stringify(json.usage) : 'n/a';
console.log(JSON.stringify({
  ok: true, out, bytes: buf.length, model, size,
  elapsed_ms: Date.now() - t0, usage,
  revised_prompt: (item.revised_prompt || '').slice(0, 300),
}, null, 2));

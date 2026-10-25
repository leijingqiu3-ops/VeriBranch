#!/usr/bin/env node
/**
 * bridge.js — 把上游仓库 portal/assets/engines.js 的纯函数引擎
 * 暴露成「JSON 进 / JSON 出」的命令行工具。
 *
 * 用法: node bridge.js <repo> <tool> <payload-json>
 *   tool ∈ impact | risk | caliber | grade | intake | report | selftest
 *
 * 约束：
 *  - 不修改上游任何文件，只 require 后调用。
 *  - 任何异常都以 {ok:false, reason} 返回，退出码保持 0，
 *    让调用方（Agent）拿到结构化失败，而不是崩溃。
 */
const fs = require('fs');
const path = require('path');

function fail(reason, extra) {
  process.stdout.write(JSON.stringify(Object.assign({ ok: false, reason: reason }, extra || {})));
  process.exit(0);
}

const repo = process.argv[2];
const tool = process.argv[3];
const payloadRaw = process.argv[4] || '{}';
if (!repo || !tool) fail('缺少参数: <repo> <tool> [payload]');

const dataPath = path.join(repo, 'portal', 'data', 'portal_data.json');
const enginePath = path.join(repo, 'portal', 'assets', 'engines.js');
if (!fs.existsSync(dataPath)) fail('找不到数据层: ' + dataPath + '（请先 git pull 上游仓库）');
if (!fs.existsSync(enginePath)) fail('找不到引擎: ' + enginePath);

let payload;
try { payload = JSON.parse(payloadRaw); } catch (e) { fail('payload 不是合法 JSON: ' + e.message); }

let E, D;
try {
  D = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
  global.SDP_DATA = D;
  require(enginePath);
  E = globalThis.SDP_ENGINE;
  if (!E) fail('engines.js 没有挂出 SDP_ENGINE');
} catch (e) {
  fail('加载引擎失败: ' + e.message);
}

const calls = {
  impact: () => E.impact(payload, D),
  risk: () => E.risk(payload, D),
  caliber: () => E.caliber(payload, D),
  grade: () => E.gradeFromData(D, payload.exogenous, payload.opts),
  intake: () => E.intake(payload.answers || payload),
  report: () => ({ ok: true, markdown: E.buildReport(payload.project || payload, D) }),
  meta: () => ({
    ok: true,
    data_generated_at: (D.meta || {}).generated_at || null,
    top_keys: Object.keys(D),
    tools: Object.keys(E),
  }),
  selftest: () => ({ ok: true, exports: Object.keys(E), data_keys: Object.keys(D).length }),
};

if (!calls[tool]) fail('未知工具: ' + tool + '（可选: ' + Object.keys(calls).join(', ') + '）');

try {
  const out = calls[tool]();
  process.stdout.write(JSON.stringify(out === undefined ? { ok: false, reason: '引擎返回 undefined' } : out));
} catch (e) {
  fail('引擎执行异常: ' + e.message, { tool: tool });
}
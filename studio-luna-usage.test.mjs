import {test} from 'node:test';
import assert from 'node:assert/strict';
import {lunaUsage,lunaUsageHTML} from './studio-luna-usage.mjs';
test('count settled child receipts including failures once, never parent totals or reused plans',()=>{
  const rows=[{stage:'visual',provider:'openai-luna',detail:true,attempt:2,inputTokens:100,tokens:30,estimatedUSD:.01},
    {stage:'facts',provider:'openai-luna',detail:true,inputTokens:40,tokens:10,estimatedUSD:.005,status:'FAILED'},
    {stage:'AI directing',inputTokens:140,tokens:40,estimatedUSD:.015},
    {stage:'visual',provider:'openai-luna',detail:true,reused:true,inputTokens:100,tokens:30}];
  const result=lunaUsage(rows);
  assert.equal(result.calls,3);assert.equal(result.input,140);assert.equal(result.output,40);assert.equal(result.cost,.015);
  assert.equal(result.rows[0].stage,'visual');assert.deepEqual(rows[1].status,'FAILED');
});
test('unknown usage stays unknown and public HTML never shows raw API contexts',()=>{
  const rows=[{stage:'<script>',provider:'openai-luna',detail:true,inputTokens:4,tokens:2,usagePending:true},
    {stage:'missing',provider:'openai-luna',detail:true,inputTokens:null,tokens:null,rawContext:'private story'}];
  const html=lunaUsageHTML(rows);
  assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('lack token totals'));
  assert.ok(html.includes('not the size of your story'));assert.ok(!html.includes('private story'));
  assert.equal(lunaUsage(rows).pending,1);assert.equal(lunaUsage(rows).missing,1);
  assert.equal(lunaUsage(rows).costMissing,1);
  assert.ok(html.includes('Unknown'));
});

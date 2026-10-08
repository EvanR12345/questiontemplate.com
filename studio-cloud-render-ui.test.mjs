import test from 'node:test';
import assert from 'node:assert/strict';
import {CloudRenderClient,cloudRenderForm} from './studio-cloud-render-ui.mjs';
const project={id:'pr-0123456789abcdef',revision:4,settings:{engagement:{exportDestination:'patreon'}}};
test('an ambiguous start retains its exact nonce and sends only control JSON',async()=>{
  const calls=[];let lost=true;
  const client=new CloudRenderClient(project,async(path,body)=>{
    calls.push(body);assert.equal(path,'cloud-render');
    if(lost){lost=false;throw Error('Lost reply');}
    return {project:project.id,id:body.options.id,status:'SUBMISSION_UNKNOWN'};
  },()=> 'a'.repeat(32));
  await assert.rejects(client.start('patreon'));
  await client.start('patreon');
  assert.deepEqual(calls[0],calls[1]);assert.equal(calls.length,2);
  assert.ok(JSON.stringify(calls[0]).length<240);assert.equal(client.busy,false);
  await assert.rejects(client.start('youtube'),/already active/);
});
test('refresh after browser restart discovers the saved project job before cancellation',async()=>{
  const calls=[];const client=new CloudRenderClient(project,async(_,body)=>{
    calls.push(body);return {project:project.id,id:'b'.repeat(32),status:'RENDERING',cancelRequested:body.operation==='cancel'};
  });
  await client.refresh();await client.cancel();
  assert.deepEqual(calls[0],{project:project.id,operation:'status'});
  assert.equal(calls[1].options.id,'b'.repeat(32));assert.equal(client.state.cancelRequested,true);
});
test('render controls begin disabled and keep the destination choice explicit',()=>{
  const html=cloudRenderForm(project);
  assert.match(html,/id="cloudRenderStart" disabled/);assert.match(html,/value="patreon" selected/);
  assert.ok(html.includes('trial compute credits'));assert.ok(!html.includes('apiKey'));
});
test('a delayed progress reply cannot erase a newer cancellation request',async()=>{
  let release;const delayed=new Promise(resolve=>{release=resolve;});
  const client=new CloudRenderClient(project,async(_,body)=>body.operation==='status'?delayed:
    {id:'b'.repeat(32),project:project.id,status:'RENDERING',cancelRequested:true});
  client.state={id:'b'.repeat(32),status:'RENDERING'};
  const earlier=client.refresh();await client.cancel();
  release({id:'b'.repeat(32),status:'RENDERING',cancelRequested:false});await earlier;
  assert.equal(client.state.cancelRequested,true);
});

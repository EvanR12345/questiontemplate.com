import test from 'node:test';
import assert from 'node:assert/strict';
import {importHelperPairing,KEY_STORAGE} from './helper-connection.mjs';

test('private file pairing accepts only the exact local helper credential format',()=>{
  const writes=[];
  assert.equal(importHelperPairing('  '+ 'a'.repeat(64)+'\n',{setItem:(...value)=>writes.push(value)}),'a'.repeat(64));
  assert.deepEqual(writes,[[KEY_STORAGE,'a'.repeat(64)]]);
  for(const value of ['api-key','a'.repeat(63),'g'.repeat(64),'{"private_key":"credential"}',null,'https://attacker.example/#native='+ 'a'.repeat(64)]){
    assert.throws(()=>importHelperPairing(value,{setItem(){throw Error('must not store invalid input');}}),/helper pairing file/);
  }
  assert.equal(writes.length,1);
});

test('blocked persistent browser storage retains only an in-session pairing',()=>{
  assert.equal(importHelperPairing('b'.repeat(64),{setItem(){throw Error('storage blocked');}}),'b'.repeat(64));
});

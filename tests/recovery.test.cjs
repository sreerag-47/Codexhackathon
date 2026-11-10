const {test} = require('node:test');
const assert = require('node:assert/strict');
const {Recovery,validReceipts} = require('../static/recovery.js');
class MemoryStore {
  constructor(){this.values=new Map();}
  async get(k){return this.values.get(k);}
  async set(k,v){this.values.set(k,structuredClone(v));}
  async delete(k){this.values.delete(k);}
}
const receipt={report_id:'report-'+'a'.repeat(32),ticket_id:'CP-000001',token:'random-private-token-12345678'};
test('draft retains photo bytes and uncertain submission key without network access',async()=>{
  const recovery=new Recovery(new MemoryStore());
  await recovery.save('draft',{fields:{transcript:'Broken road'},photo:new Blob(['photo evidence']),pendingKey:'pending-private-key'});
  const draft=await recovery.load('draft');
  assert.equal(await draft.photo.text(),'photo evidence');assert.equal(draft.pendingKey,'pending-private-key');
});
test('recovery expires after seven inactive days and removes the stored record',async()=>{
  let now=100;const store=new MemoryStore();const recovery=new Recovery(store,()=>now);
  await recovery.save('receipts',[receipt]);now+=7*86400000;
  assert.equal(await recovery.load('receipts'),null);assert.equal(store.values.has('receipts'),false);
});
test('invalid imported receipts cannot supply arbitrary paths or oversized token lists',()=>{
  assert.deepEqual(validReceipts([receipt]),[receipt]);
  assert.throws(()=>validReceipts([{...receipt,report_id:'../../secrets'}]));
  assert.throws(()=>validReceipts([{...receipt,token:''}]));
  assert.throws(()=>validReceipts(Array(1001).fill(receipt)));
});
test('clearing local data wins over earlier queued autosaves',async()=>{
  const store=new MemoryStore();const recovery=new Recovery(store);
  const save=recovery.save('draft',{fields:{transcript:'private'}});const clear=recovery.clear();
  await Promise.all([save,clear]);assert.equal(await recovery.load('draft'),null);
});
test('storage failure is surfaced and does not poison later recovery operations',async()=>{
  const store=new MemoryStore();const recovery=new Recovery(store);
  store.set=async()=>{throw new Error('quota exceeded');};
  await assert.rejects(recovery.save('receipts',[receipt]),/quota/);
  await recovery.clear();assert.equal(await recovery.load('draft'),null);
});

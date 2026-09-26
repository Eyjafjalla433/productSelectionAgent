import { it, expect } from 'vitest';
import { ApiClient, ApiError } from '../src/api';

it('does not replay failed mutations and retains the error code', async () => {
  let calls=0;
  const client=new ApiClient(async()=>{ calls++;return new Response(JSON.stringify({error:'Expired',error_code:'session_expired'}),{status:404}); });
  await expect(client.post('/api/chat',{session_id:'s',message:'hello'})).rejects.toMatchObject({code:'session_expired'});
  expect(calls).toBe(1);
});
it('serializes requests in a session rather than racing', async () => {
  let active=0;let peak=0;
  const client=new ApiClient(async()=>{active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,10));active--;return new Response('{}');});
  await Promise.all([client.post('/api/select',{}),client.post('/api/chat',{})]);
  expect(peak).toBe(1);
});
it('reports invalid response bodies without retrying', async()=>{
  const client=new ApiClient(async()=>new Response('<html>error</html>'));
  await expect(client.post('/api/chat',{})).rejects.toBeInstanceOf(ApiError);
});
it('calls native fetch with its browser receiver', async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async function(this:unknown){if(this!==globalThis)throw new TypeError('Illegal invocation');return new Response('{"ok":true}');} as typeof fetch;
  try {await expect(new ApiClient().get('/api/health')).resolves.toEqual({ok:true});}
  finally {globalThis.fetch=original;}
});

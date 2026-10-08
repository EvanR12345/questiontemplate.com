import test from 'node:test';
import assert from 'node:assert/strict';
import {identityToken,federatedAccessToken,federationSettings} from './federation.mjs';
import {handle} from './worker.mjs';
import {publisherBucket} from './google-bucket.mjs';

async function fixture(){
  const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
  return {pair,env:{STUDIO_WIF_ISSUER:'https://private-studio.example.workers.dev',STUDIO_WIF_AUDIENCE:'//iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/studio/providers/publisher',STUDIO_GCS_SERVICE_ACCOUNT_EMAIL:'studio-storage@test-project.iam.gserviceaccount.com',STUDIO_WIF_KEY_ID:'test-'+crypto.randomUUID(),STUDIO_WIF_PRIVATE_KEY:`-----BEGIN PRIVATE KEY-----\n${Buffer.from(await crypto.subtle.exportKey('pkcs8',pair.privateKey)).toString('base64')}\n-----END PRIVATE KEY-----`,PUBLISHER_TOKEN:'x'.repeat(64)}};
}
test('workload tokens preserve exact issuer, sole subject, audience and five-minute expiry',async()=>{
  const {pair,env}=await fixture(),token=await identityToken(env),[header,payload,signature]=token.split('.');
  const claims=JSON.parse(Buffer.from(payload,'base64url'));
  assert.equal(claims.iss,env.STUDIO_WIF_ISSUER);assert.equal(claims.sub,'studio-publisher');assert.equal(claims.aud,env.STUDIO_WIF_AUDIENCE);assert.equal(claims.exp-claims.iat,300);
  assert.ok(await crypto.subtle.verify('RSASSA-PKCS1-v1_5',pair.publicKey,Buffer.from(signature,'base64url'),new TextEncoder().encode(header+'.'+payload)));
});
test('render tokens have a separate cache and scope from bucket-only tokens',async()=>{
  const {env}=await fixture();const scopes=[];
  const fetcher=async(url,options)=>{
    if(url==='https://sts.googleapis.com/v1/token')return Response.json({access_token:'test-federated'});
    const scope=JSON.parse(options.body).scope[0];scopes.push(scope);
    return Response.json({accessToken:'test-'+scopes.length,expireTime:new Date(Date.now()+3600000).toISOString()});
  };
  assert.equal(await federatedAccessToken(env,fetcher),'test-1');
  assert.equal(await federatedAccessToken(env,fetcher,'render'),'test-2');
  assert.equal(await federatedAccessToken(env,fetcher),'test-1');
  assert.deepEqual(scopes,['https://www.googleapis.com/auth/devstorage.read_write','https://www.googleapis.com/auth/cloud-platform']);
  await assert.rejects(federatedAccessToken(env,fetcher,'arbitrary'));
});
test('private identity endpoint requires existing publisher authentication and never calls Google itself',async()=>{
  const {env}=await fixture();let calls=0;
  const fetcher=async()=>{calls++;throw Error('No network permitted');};
  const unauthenticated=await handle(new Request(env.STUDIO_WIF_ISSUER+'/identity/token'),env,fetcher);
  assert.equal(unauthenticated.status,401);
  const authenticated=await handle(new Request(env.STUDIO_WIF_ISSUER+'/identity/token',{headers:{Authorization:'Bearer '+env.PUBLISHER_TOKEN}}),env,fetcher);
  assert.equal(authenticated.status,200);assert.equal(calls,0);
  const value=await authenticated.json();assert.equal(Object.keys(value).join(','),'token');assert.ok(!value.token.includes('PRIVATE KEY'));
  assert.equal(authenticated.headers.get('Cache-Control'),'no-store');
});
test('keyless access exchanges only with Google STS and the single bucket identity, caches the resulting token',async()=>{
  const {env}=await fixture();let calls=0;
  const fetcher=async(url,options)=>{
    calls++;assert.equal(options.redirect,'error');
    const body=JSON.parse(options.body);
    if(url==='https://sts.googleapis.com/v1/token'){
      assert.equal(body.audience,env.STUDIO_WIF_AUDIENCE);assert.equal(body.subjectTokenType,'urn:ietf:params:oauth:token-type:jwt');
      return Response.json({access_token:'test-federated-access'});
    }
    assert.equal(url,'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'+encodeURIComponent(env.STUDIO_GCS_SERVICE_ACCOUNT_EMAIL)+':generateAccessToken');
    assert.equal(options.headers.Authorization,'Bearer test-federated-access');
    assert.deepEqual(body.scope,['https://www.googleapis.com/auth/devstorage.read_write']);
    return Response.json({accessToken:'test-bucket-access',expireTime:new Date(Date.now()+3600000).toISOString()});
  };
  assert.equal(await federatedAccessToken(env,fetcher),'test-bucket-access');
  assert.equal(await federatedAccessToken(env,fetcher),'test-bucket-access');assert.equal(calls,2);
  const bucket=publisherBucket({...env,STUDIO_STORAGE_PROVIDER:'gcs',STUDIO_GCS_AUTH_MODE:'federated',STUDIO_GCS_BUCKET:'private-test',STUDIO:{bad:true}},fetcher);
  assert.equal(await bucket.accessToken(),'test-bucket-access');assert.equal(calls,2);
});
test('invalid identity endpoints and failed federation cannot silently fall back to R2',async()=>{
  const {env}=await fixture();
  for(const issuer of ['http://private.workers.dev','https://private.workers.dev/?secret=x','https://user:password@private.workers.dev','https://attacker.example'])assert.throws(()=>federationSettings({...env,STUDIO_WIF_ISSUER:issuer}),/issuer/);
  await assert.rejects(federatedAccessToken(env,async()=>new Response(null,{status:403})),/exchange failed/);
  const bucket=publisherBucket({...env,STUDIO_STORAGE_PROVIDER:'gcs',STUDIO_GCS_AUTH_MODE:'federated',STUDIO_GCS_BUCKET:'private-test',STUDIO:{}},async()=>new Response(null,{status:403}));
  await assert.rejects(bucket.head('anything'),/exchange failed/);
});
test('public discovery exposes only verification material while token issuance stays private',async()=>{
  const {pair,env}=await fixture();
  const publicKey=await crypto.subtle.exportKey('jwk',pair.publicKey);
  const key={kty:publicKey.kty,n:publicKey.n,e:publicKey.e,alg:'RS256',use:'sig',kid:env.STUDIO_WIF_KEY_ID};
  env.STUDIO_WIF_PUBLIC_JWKS=JSON.stringify({keys:[key]});
  const fetcher=async()=>{throw Error('No network permitted');};
  const metadata=await handle(new Request(env.STUDIO_WIF_ISSUER+'/.well-known/openid-configuration'),env,fetcher);
  assert.equal(metadata.status,200);assert.equal((await metadata.json()).jwks_uri,env.STUDIO_WIF_ISSUER+'/identity/jwks');
  const jwks=await handle(new Request(env.STUDIO_WIF_ISSUER+'/identity/jwks'),env,fetcher);
  assert.deepEqual(await jwks.json(),{keys:[key]});
  assert.equal((await handle(new Request(env.STUDIO_WIF_ISSUER+'/identity/token'),env,fetcher)).status,401);
  env.STUDIO_WIF_PUBLIC_JWKS=JSON.stringify({keys:[{...key,d:'must-never-be-public'}]});
  const invalid=await handle(new Request(env.STUDIO_WIF_ISSUER+'/identity/jwks'),env,fetcher);
  assert.equal(invalid.status,400);assert.ok(!(await invalid.text()).includes('must-never-be-public'));
});

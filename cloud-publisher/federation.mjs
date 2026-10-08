// Keyless Google authentication: the private publisher is the sole OIDC issuer.
// Google stores only its public verification key, never this signing secret.
const accessTokens=new Map();
const encode=value=>BufferlessBase64(new TextEncoder().encode(JSON.stringify(value)));
function BufferlessBase64(bytes){return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');}
export function federationSettings(env){
  const issuer=env.STUDIO_WIF_ISSUER,audience=env.STUDIO_WIF_AUDIENCE,email=env.STUDIO_GCS_SERVICE_ACCOUNT_EMAIL,kid=env.STUDIO_WIF_KEY_ID;
  let url;try{url=new URL(issuer);}catch{throw Error('Configure the private Studio identity issuer.');}
  if(url.protocol!=='https:'||!url.hostname.endsWith('.workers.dev')||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw Error('Invalid private Studio identity issuer.');
  if(!/^\/\/iam\.googleapis\.com\/projects\/\d+\/locations\/global\/workloadIdentityPools\/[a-z0-9-]+\/providers\/[a-z0-9-]+$/.test(audience??''))throw Error('Invalid Studio workload identity audience.');
  if(!/^[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com$/.test(email??'')||!/^[a-zA-Z0-9-]{1,80}$/.test(kid??'')||!env.STUDIO_WIF_PRIVATE_KEY)throw Error('Configure the private Studio workload identity.');
  return {issuer:url.origin,audience,email,kid};
}
export async function identityToken(env){
  const settings=federationSettings(env),time=Math.floor(Date.now()/1000);
  const header=encode({alg:'RS256',typ:'JWT',kid:settings.kid});
  const payload=encode({iss:settings.issuer,sub:'studio-publisher',aud:settings.audience,iat:time,exp:time+300});
  const pem=env.STUDIO_WIF_PRIVATE_KEY.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g,'');
  const key=await crypto.subtle.importKey('pkcs8',Uint8Array.from(atob(pem),char=>char.charCodeAt(0)),{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['sign']);
  const signed=header+'.'+payload;
  return signed+'.'+BufferlessBase64(new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',key,new TextEncoder().encode(signed))));
}
export function publicFederationDocument(env,path){
  const settings=federationSettings(env);
  const publicKeys=JSON.parse(env.STUDIO_WIF_PUBLIC_JWKS??'null');
  if(!publicKeys||Object.keys(publicKeys).join(',')!=='keys'||!Array.isArray(publicKeys.keys)||publicKeys.keys.length!==1)
    throw Error('Configure the public Studio verification key.');
  const key=publicKeys.keys[0];
  const allowed=['kty','n','e','alg','use','kid'];
  if(Object.keys(key).some(k=>!allowed.includes(k))||key.kty!=='RSA'||key.alg!=='RS256'||key.use!=='sig'||
     key.kid!==settings.kid||!/^[A-Za-z0-9_-]{256,}$/.test(key.n??'')||!/^[A-Za-z0-9_-]+$/.test(key.e??''))
    throw Error('Invalid public Studio verification key.');
  if(path==='/identity/jwks')return publicKeys;
  return {issuer:settings.issuer,jwks_uri:settings.issuer+'/identity/jwks',
    id_token_signing_alg_values_supported:['RS256'],subject_types_supported:['public'],response_types_supported:['id_token']};
}
export async function federatedAccessToken(env,fetcher=fetch,purpose='storage'){
  if(!['storage','render'].includes(purpose))throw Error('Invalid private identity purpose.');
  const scope=purpose==='render'?'https://www.googleapis.com/auth/cloud-platform':'https://www.googleapis.com/auth/devstorage.read_write';
  const settings=federationSettings(env),cacheKey=settings.audience+'|'+settings.email+'|'+settings.kid+'|'+purpose;
  const old=accessTokens.get(cacheKey);if(old?.expires>Date.now()+60000)return old.value;
  const token=await identityToken(env);
  const exchanged=await fetcher('https://sts.googleapis.com/v1/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({audience:settings.audience,grantType:'urn:ietf:params:oauth:grant-type:token-exchange',requestedTokenType:'urn:ietf:params:oauth:token-type:access_token',scope:'https://www.googleapis.com/auth/cloud-platform',subjectTokenType:'urn:ietf:params:oauth:token-type:jwt',subjectToken:token}),redirect:'manual'});
  if(!exchanged.ok)throw Error('Google workload identity exchange failed. Verify the private issuer grant.');
  const exchangedToken=await exchanged.json();if(!exchangedToken.access_token)throw Error('Google workload identity returned no access token.');
  const result=await fetcher('https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'+encodeURIComponent(settings.email)+':generateAccessToken',{method:'POST',headers:{Authorization:'Bearer '+exchangedToken.access_token,'Content-Type':'application/json'},body:JSON.stringify({scope:[scope],lifetime:'3600s'}),redirect:'manual'});
  if(!result.ok)throw Error('Google bucket identity impersonation failed. Check the service-account-only grant.');
  const value=await result.json(),expires=Date.parse(value.expireTime);
  if(!value.accessToken||!Number.isFinite(expires)||expires<=Date.now())throw Error('Google bucket authorization is expired.');
  if(accessTokens.size>=4)accessTokens.delete(accessTokens.keys().next().value);
  accessTokens.set(cacheKey,{value:value.accessToken,expires:Math.min(expires,Date.now()+3600000)});
  return value.accessToken;
}

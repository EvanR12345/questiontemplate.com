"""Loopback HTTP fixtures only; no GPU, external API, or automatic retries."""
import io,json,socket,threading,time,unittest,urllib.error,urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
from image_provider import ComfyHTTPPool,ComfyImageProvider,RemoteGenerationUncertain
from studio_image_workers import ComfyWorkerPool


class HTTPPoolTest(unittest.TestCase):
    def setUp(self):
        self.events=[];self.connections=0;self.lock=threading.Lock();self.pools=[];self.comfy_success=False
        self.entered=threading.Event();self.release=threading.Event();owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def setup(self):
                super().setup()
                with owner.lock:owner.connections+=1;self.connection_id=owner.connections
            def log_message(self,*_args):pass
            def do_GET(self):self.dispatch()
            def do_POST(self):self.dispatch()
            def dispatch(self):
                body=self.rfile.read(int(self.headers.get('Content-Length',0)))
                with owner.lock:owner.events.append({'path':self.path,'method':self.command,'body':body,'connection':self.connection_id,
                    'contentType':self.headers.get('Content-Type'),'userAgent':self.headers.get('User-Agent'),
                    'acceptEncoding':self.headers.get('Accept-Encoding')})
                if owner.comfy_success:
                    if self.path=='/prompt':payload=json.dumps({'prompt_id':'accepted'}).encode()
                    elif self.path.startswith('/history/'):
                        payload=json.dumps({'accepted':{'outputs':{'1':{'images':[{'filename':'saved.png'}]}},'status':{'status_str':'success'}}}).encode()
                    elif self.path.startswith('/view?'):
                        buffer=io.BytesIO();Image.new('RGB',(8,8),(120,160,200)).save(buffer,'PNG');payload=buffer.getvalue()
                    else:raise AssertionError(self.path)
                    self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);self.wfile.flush();return
                if self.path=='/hold':owner.entered.set();owner.release.wait(3)
                if self.path=='/prompt':
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();self.close_connection=True;return
                if self.path=='/partial':
                    self.send_response(200);self.send_header('Content-Length','20');self.end_headers();self.wfile.write(b'short');self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();self.close_connection=True;return
                status=400 if self.path=='/reject' else 302 if self.path=='/redirect' else 200
                payload=json.dumps({'path':self.path,'body':body.decode(),'connection':self.connection_id}).encode()
                self.send_response(status)
                if self.path=='/redirect':self.send_header('Location','http://example.invalid/private')
                if self.path=='/chunked':
                    self.send_header('Transfer-Encoding','chunked');self.end_headers()
                    for chunk in (payload[:5],payload[5:]):self.wfile.write(hex(len(chunk))[2:].encode()+b'\r\n'+chunk+b'\r\n')
                    self.wfile.write(b'0\r\n\r\n');self.wfile.flush();return
                self.send_header('Content-Length',str(len(payload)))
                if self.path=='/close':self.send_header('Connection','close');self.close_connection=True
                self.end_headers();self.wfile.write(payload);self.wfile.flush()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.release.set()
        for pool in self.pools:pool.invalidate()
        self.server.shutdown();self.server.server_close();self.thread.join(2)
    def pool(self,**kwargs):
        p=ComfyHTTPPool(self.url,**kwargs);self.pools.append(p);return p

    def test_content_length_requests_reuse_one_socket_without_changing_json_bytes(self):
        p=self.pool();body={'prompt':{'text':'Exact characters é','seed':42},'refs':['exact']}
        for _ in range(3):self.assertEqual(p.json(self.url+'/echo',body)['body'],json.dumps(body))
        self.assertEqual(self.connections,1);self.assertEqual(len(self.events),3)
        self.assertTrue(all(e['body']==json.dumps(body).encode() for e in self.events))

    def test_chunked_response_is_consumed_before_socket_reuse(self):
        p=self.pool();self.assertEqual(p.json(self.url+'/chunked')['path'],'/chunked')
        self.assertEqual(p.json(self.url+'/next')['path'],'/next');self.assertEqual(self.connections,1)

    def test_concurrent_replies_and_requests_cannot_mix_and_capacity_is_bounded(self):
        p=self.pool(capacity=2)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results=list(executor.map(lambda i:p.json(self.url+'/echo?i='+str(i),{'i':i}),range(20)))
        self.assertLessEqual(self.connections,2)
        for index,result in enumerate(results):
            self.assertEqual(result['path'],'/echo?i='+str(index));self.assertEqual(json.loads(result['body']),{'i':index})
        self.assertEqual(len(self.events),20);self.assertEqual(p.active,0)

    def test_invalidation_retires_idle_socket_and_a_response_currently_being_read(self):
        p=self.pool();first=p.json(self.url+'/first')['connection'];p.invalidate()
        second=p.json(self.url+'/second')['connection'];self.assertNotEqual(first,second)
        with ThreadPoolExecutor(max_workers=1) as executor:
            task=executor.submit(p.json,self.url+'/hold');self.assertTrue(self.entered.wait(2))
            p.invalidate();self.release.set();self.assertEqual(task.result()['path'],'/hold')
        self.assertEqual(p.idle,[])
        self.assertNotEqual(p.json(self.url+'/third')['connection'],second)

    def test_idle_expiry_and_server_close_require_fresh_connections(self):
        p=self.pool(idle_seconds=0)
        self.assertNotEqual(p.json(self.url+'/one')['connection'],p.json(self.url+'/two')['connection'])
        p=self.pool();closed=p.json(self.url+'/close')['connection']
        self.assertNotEqual(closed,p.json(self.url+'/after-close')['connection'])

    def test_separate_provider_instances_do_not_share_connections(self):
        a,b=self.pool(),self.pool()
        self.assertNotEqual(a.json(self.url+'/one')['connection'],b.json(self.url+'/two')['connection'])

    def test_http_errors_and_redirects_are_not_retried_or_followed(self):
        p=self.pool()
        for path,status in (('/reject',400),('/redirect',302)):
            with self.assertRaises(urllib.error.HTTPError) as error:p.json(self.url+path,{'exact':'body'})
            self.assertEqual(error.exception.code,status)
        self.assertEqual([e['path'] for e in self.events],['/reject','/redirect'])

    def test_partial_response_and_lost_post_ack_are_not_replayed(self):
        p=self.pool()
        with self.assertRaises(OSError):p.json(self.url+'/partial')
        with self.assertRaises(OSError):p.json(self.url+'/prompt',{'seed':42})
        self.assertEqual([e['path'] for e in self.events],['/partial','/prompt'])
        self.assertEqual(p.active,0);self.assertEqual(p.idle,[])

    def test_lost_post_ack_on_reused_socket_is_uncertain_without_second_dispatch(self):
        p=self.pool();first=p.json(self.url+'/ready')['connection']
        with self.assertRaises(OSError):p.json(self.url+'/prompt',{'seed':42})
        self.assertEqual([e['path'] for e in self.events],['/ready','/prompt'])
        self.assertEqual([e['connection'] for e in self.events],[first,first])
        self.assertEqual(p.idle,[])

    def test_actual_provider_success_preserves_request_headers_graph_and_image_pixels(self):
        self.comfy_success=True;outcomes=[];posts=[]
        for overrides in ({'comfyPersistentHTTP':False},{'comfyPersistentHTTP':True},{},{'comfyPersistentHTTP':None}):
            config={'comfyEndpoint':self.url,**overrides};before=dict(config)
            p=ComfyImageProvider(config)
            self.assertEqual(config,before)
            self.assertEqual(p._transport is not None,overrides.get('comfyPersistentHTTP',True) is True)
            if p._transport:self.pools.append(p._transport)
            p.validateSettings=lambda s:s;p.healthCheck=lambda **_:{'installed':True}
            p.template=lambda:{'model':'flux','name':'test','prompt':{'1':{'class_type':'Test','inputs':{}}},'bindings':{}}
            outcome=p.generateImage({'prompt':'A landscape','settings':{'model':'flux','seed':17,'steps':4}},lambda *_:None)
            outcomes.append((outcome['pil'].tobytes(),outcome['settings'],outcome['remotePromptId']))
            post=next(e for e in reversed(self.events) if e['path']=='/prompt');body=json.loads(post['body']);body.pop('client_id')
            posts.append((body,post['contentType'],post['userAgent'],post['acceptEncoding']))
        self.assertTrue(all(row==outcomes[0] for row in outcomes))
        self.assertTrue(all(row==posts[0] for row in posts))

    def test_endpoint_authority_and_credentials_cannot_escape(self):
        p=self.pool()
        for url in ('http://example.invalid/x',self.url.replace('127.0.0.1','localhost')+'/x',
                    self.url.replace('http://','http://secret@')+'/x',self.url+'/x#fragment'):
            with self.assertRaises(ValueError):p.json(url)
        self.assertEqual(self.events,[])

    def test_actual_provider_classifies_lost_ack_as_uncertain_and_quarantines_pool(self):
        config={'comfyEndpoint':self.url,'comfyModelCopies':1,'comfyPersistentHTTP':True}
        pool=ComfyWorkerPool(config);worker=pool.workers[0];self.pools.append(worker._transport)
        worker.validateSettings=lambda s:s;worker.healthCheck=lambda **_:{'installed':True}
        worker.template=lambda:{'model':'flux','name':'test','prompt':{'1':{'class_type':'Test','inputs':{}}},'bindings':{}}
        pool.confirm_workers({'gpu':'NVIDIA GeForce RTX 5090','workers':[{'pid':1,'port':8188}]})
        request={'prompt':'exact','settings':{'model':'flux','seed':17,'steps':4}}
        with self.assertRaises(RemoteGenerationUncertain):pool.generateImage(request,lambda *_:None)
        self.assertTrue(pool.quarantined)
        with self.assertRaisesRegex(RuntimeError,'unresolved'):pool.generateImage(request,lambda *_:None)
        self.assertEqual(len(self.events),1)

    def test_default_on_with_explicit_opt_out_and_worker_reset_invalidation(self):
        default=ComfyImageProvider({'comfyEndpoint':self.url});self.pools.append(default._transport)
        self.assertIsInstance(default._transport,ComfyHTTPPool)
        for value in (None,False,'true',1):
            self.assertIsNone(ComfyImageProvider({'comfyPersistentHTTP':value})._transport)
        provider=ComfyImageProvider({'comfyEndpoint':self.url,'comfyPersistentHTTP':True});self.pools.append(provider._transport)
        first=provider._request_json(self.url+'/one')['connection'];provider._uploads['hash']='old.png'
        provider.invalidate_backend_cache()
        self.assertEqual(provider._uploads,{})
        self.assertNotEqual(first,provider._request_json(self.url+'/two')['connection'])

if __name__=='__main__':unittest.main()

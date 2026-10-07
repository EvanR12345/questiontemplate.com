"""QuestionTemplate local NVIDIA bridge: Audio + Studio. Bound to localhost only."""
import base64
import hmac
import io
import json
import os
import secrets
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VOICES = {'am_michael', 'am_fenrir', 'am_puck', 'af_heart', 'af_bella', 'af_nicole', 'bm_george', 'bf_emma'}
PORT = 8765
MAX_BODY = 32 * 1024 * 1024
MODEL_ID = os.environ.get('QT_IMAGE_MODEL', 'stable-diffusion-v1-5/stable-diffusion-v1-5')
INPAINT_ID = os.environ.get('QT_INPAINT_MODEL', 'runwayml/stable-diffusion-inpainting')


class CudaEngine:
    def __init__(self):
        import torch
        from huggingface_hub import hf_hub_download
        from kokoro import KModel
        self.torch, self.download = torch, hf_hub_download
        if not torch.cuda.is_available():
            raise RuntimeError('NVIDIA CUDA is unavailable. Update the NVIDIA driver and run the helper again.')
        index = max(range(torch.cuda.device_count()), key=lambda i: torch.cuda.get_device_properties(i).total_memory)
        self.index = index
        self.device = f'cuda:{index}'
        torch.cuda.set_device(index)
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        torch.backends.cudnn.benchmark = False
        self.model = KModel(repo_id='hexgrad/Kokoro-82M').to(self.device).eval()
        self.voices = {}
        self.warm = set()
        self.gpu = torch.cuda.get_device_name(index)
        self.prepare('am_michael')

    def health(self):
        return {
            'protocol': 2, 'backend': 'cuda', 'gpu': self.gpu, 'sampleRate': 24000,
            'precision': 'float32', 'vocab': self.model.vocab,
            'capabilities': ['audio', 'image', 'image-queue'], 'imageModel': MODEL_ID
        }

    def prepare(self, voice):
        if voice not in VOICES:
            raise ValueError('Choose a supported studio voice.')
        if voice not in self.voices:
            path = self.download(repo_id='hexgrad/Kokoro-82M', filename=f'voices/{voice}.pt')
            self.voices[voice] = self.torch.load(path, map_location='cpu', weights_only=True).to(self.device)
        if voice not in self.warm:
            self.render('həlˈoʊ.', voice, 1.0)
            self.warm.add(voice)

    def render(self, phonemes, voice, speed):
        torch = self.torch
        if not phonemes or any(char not in self.model.vocab for char in phonemes):
            raise ValueError('Speech sounds do not match the loaded model.')
        if len(phonemes) + 2 > min(280, self.model.context_length):
            raise ValueError('Section exceeds the model limit. No audio was truncated.')
        with torch.inference_mode():
            audio = self.model(phonemes, self.voices[voice][len(phonemes) - 1], speed)
        if not torch.isfinite(audio).all():
            raise RuntimeError('Model returned invalid audio.')
        return audio.float().cpu().numpy().astype('<f4', copy=False).tobytes()

    def synthesize(self, phonemes, voice, speed):
        self.prepare(voice)
        return self.render(phonemes, voice, speed)

    def synthesize_timed(self, phonemes, voice, speed):
        """Same loaded voice, plus model durations for connected sentence timing."""
        self.prepare(voice)
        torch = self.torch
        if not phonemes or any(c not in self.model.vocab for c in phonemes):
            raise ValueError('Speech sounds do not match the loaded model.')
        if len(phonemes) + 2 > min(280, self.model.context_length):
            raise ValueError('Section exceeds the model limit. No audio was truncated.')
        with torch.inference_mode():
            output = self.model(phonemes, self.voices[voice][len(phonemes)-1], speed, return_output=True)
        if not torch.isfinite(output.audio).all():
            raise RuntimeError('Model returned invalid audio.')
        durations = output.pred_dur
        if durations is None or durations.numel() != len(phonemes) + 2:
            raise RuntimeError('Voice did not return valid timing for connected narration.')
        weights = durations.float().cpu().numpy()
        weights[1] += weights[0]
        weights[-2] += weights[-1]
        return output.audio.float().cpu().numpy().astype('<f4', copy=False).tobytes(), weights[1:-1].tolist()


from image_engine import ImageEngine
from image_queue import ImageQueue
from pathlib import Path
import mimetypes
from urllib.parse import unquote, parse_qs
from studio_service import StudioService
from studio_data import new_project, new_chapter, character, get_chapter, get_shot, uid, clean_narration
import copy

WEB_ROOT = Path(os.environ.get('QT_WEB_ROOT', str(Path(__file__).resolve().parent / 'web'))).resolve()

def allowed_origin(origin):
    if origin in ('https://questiontemplate.com', 'https://www.questiontemplate.com'):
        return True
    parsed = urlparse(origin or '')
    return parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')


def make_handler(audio_engine, key, queue_root=None, image_factory=None):
    gpu_lock = getattr(audio_engine, "lock", threading.Lock())
    image_engine = None

    def get_image_engine():
        nonlocal image_engine
        if image_engine is None:
            image_engine = (image_factory or ImageEngine)(
                audio_engine.torch, audio_engine.device, audio_engine.index, audio_engine.gpu, MODEL_ID, INPAINT_ID
            )
        return image_engine

    def before_image():
        audio_engine.model.to('cpu')
        audio_engine.voices = {name: voice.to('cpu') for name, voice in audio_engine.voices.items()}
        audio_engine.torch.cuda.empty_cache()

    def before_audio():
        if studio is not None:
            studio.unload_models()
        if image_engine is not None:
            image_engine.unload()
        if hasattr(audio_engine, 'model'):
            audio_engine.model.to(audio_engine.device)
            audio_engine.voices = {name: voice.to(audio_engine.device) for name, voice in audio_engine.voices.items()}

    studio = None
    queue = ImageQueue(queue_root or Path(__file__).resolve().parent / 'outputs',
                       lambda data, checkpoint: get_image_engine().generate(data, checkpoint), gpu_lock,
                       before_image if hasattr(audio_engine, 'model') else lambda: None)
    studio = StudioService(queue_root or Path(__file__).resolve().parent / 'outputs',audio_engine,get_image_engine,gpu_lock,before_audio,
                           before_image if hasattr(audio_engine,'model') else lambda: None,queue)
    media_tickets = {}

    class Handler(BaseHTTPRequestHandler):
        image_queue = queue
        studio_service = studio
        def log_message(self, *_):
            pass

        def reply(self, code, payload, binary=False, content_type=None):
            data = payload if binary else json.dumps(payload).encode()
            self.send_response(code)
            origin = self.headers.get('Origin', '')
            if allowed_origin(origin):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, Range')
                self.send_header('Access-Control-Expose-Headers', 'X-Sample-Rate, Content-Range, Content-Disposition')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', content_type or ('application/octet-stream' if binary else 'application/json'))
            if binary and content_type is None:
                self.send_header('X-Sample-Rate', '24000')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def permitted(self):
            origin = self.headers.get('Origin')
            if origin and not allowed_origin(origin):
                self.reply(403, {'error': 'This origin is not allowed.'})
                return False
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + key):
                self.reply(401, {'error': 'Pairing expired. Open the helper link again.'})
                return False
            return True

        def do_OPTIONS(self):
            if not allowed_origin(self.headers.get('Origin')):
                self.reply(403, {'error': 'This origin is not allowed.'})
            else:
                self.reply(200, {})

        def do_GET(self):
            path = urlparse(self.path).path
            if path=='/studio/media':
                ticket=parse_qs(urlparse(self.path).query).get('ticket',[''])[0]
                item=media_tickets.get(ticket)
                if not item or item['expires']<time.time():self.reply(401,{'error':'Media preview expired. Reopen preview.'});return
                self.send_asset(item['path'])
                return
            if path.startswith('/app/'):
                file = (WEB_ROOT / unquote(path[5:])).resolve()
                if not file.is_relative_to(WEB_ROOT) or any(part.startswith('.') for part in file.relative_to(WEB_ROOT).parts):
                    self.reply(404, {'error':'File not found.'})
                    return
                if file.is_dir():
                    file = file / 'index.html'
                try:
                    data = file.read_bytes()
                except (OSError, ValueError):
                    self.reply(404, {'error':'File not found.'})
                    return
                content_type = 'text/javascript' if file.suffix in ('.mjs','.js') else mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
                self.reply(200, data, binary=True, content_type=content_type)
                return
            if not self.permitted():
                return
            if path.startswith('/studio/'):
                try:
                    query=parse_qs(urlparse(self.path).query)
                    if path=='/studio/health':self.reply(200,studio.health())
                    elif path=='/studio/projects':self.reply(200,studio.store.list())
                    elif path=='/studio/project':
                        from studio_qc import project_view
                        self.reply(200,project_view(studio.store,query['id'][0]))
                    elif path=='/studio/revision':self.reply(200,studio.store.revision(query['id'][0]))
                    elif path=='/studio/queue':self.reply(200,studio.snapshot())
                    elif path=='/studio/trace':self.reply(200,studio.trace_report(query['project'][0]))
                    elif path=='/studio/storage':self.reply(200,studio.storage.status(query.get('project',[None])[0]))
                    elif path=='/studio/files':self.reply(200,studio.storage.files(query['project'][0]))
                    elif path=='/studio/publisher':self.reply(200,studio.publisher.status())
                    elif path=='/studio/config':self.reply(200,{k:v for k,v in studio.config.items() if k not in ('apiKey','fluxValidated','openaiKeyFile','runpodKeyFile','openaiApiKey','runpodApiKey')})
                    elif path=='/studio/asset':self.send_asset(studio.store.asset(query['project'][0],query['path'][0]),query.get('download',[None])[0])
                    else:self.reply(404,{'error':'Unknown studio endpoint.'})
                except (ValueError,KeyError,FileNotFoundError) as error:self.reply(404,{'error':str(error)})
                return
            if path == '/health':
                self.reply(200, audio_engine.health())
            elif path == '/image/health':
                self.reply(200, get_image_engine().health())
            elif path == '/image/queue':
                self.reply(200, queue.snapshot())
            elif path.startswith('/image/result/'):
                try:
                    self.reply(200, queue.result_path(path.rsplit('/', 1)[-1]).read_bytes(), binary=True, content_type='image/png')
                except (ValueError, FileNotFoundError) as error:
                    self.reply(404, {'error': str(error)})
            else:
                self.reply(404, {'error': 'Unknown endpoint.'})

        def send_asset(self,path,download=None):
            size=path.stat().st_size;start=0;end=size-1;partial=False
            value=self.headers.get('Range')
            if value:
                import re
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
                if not match:self.reply(416,{'error':'Invalid range.'});return
                a,b=match.groups()
                start=int(a) if a else max(0,size-int(b));end=min(int(b),size-1) if a and b else size-1
                if start>=size or end<start:self.reply(416,{'error':'Invalid range.'});return
                partial=True
            self.send_response(206 if partial else 200)
            origin=self.headers.get('Origin','')
            if allowed_origin(origin):self.send_header('Access-Control-Allow-Origin',origin);self.send_header('Vary','Origin')
            self.send_header('Content-Type',mimetypes.guess_type(str(path))[0] or 'application/octet-stream');self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','private, max-age=3600')
            if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            if download:self.send_header('Content-Disposition','attachment; filename="'+Path(download).name.replace('"','')+'"')
            self.end_headers()
            try:
                with path.open('rb') as f:
                    f.seek(start);remaining=end-start+1
                    while remaining:
                        data=f.read(min(1024*1024,remaining))
                        if not data:break
                        self.wfile.write(data);remaining-=len(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

        def studio_post(self,path,body):
            if path=='/studio/media-link':
                if getattr(studio,'storage',None) and studio.storage.enabled:
                    url=studio.storage.media_url(body['project'],body['path'])
                    if url:return {'url':url,'expires':time.time()+3600,'provider':'Cloudflare R2'}
                file=studio.store.asset(body['project'],body['path'])
                if not file.is_file():raise ValueError('Asset is unavailable.')
                ticket=secrets.token_hex(24)
                for old in list(media_tickets):
                    if media_tickets[old]['expires']<time.time():del media_tickets[old]
                media_tickets[ticket]={'path':file,'expires':time.time()+3600}
                return {'url':f'http://127.0.0.1:{PORT}/studio/media?ticket='+ticket,'expires':time.time()+3600}
            if path=='/studio/create':
                p=new_project(str(body.get('name','My story'))[:200]);legacy=body.get('legacy')
                p['settings']['video']['fps']=60
                p['settings']['engagement'].update(popupEnabled=True,outroEnabled=True)
                if studio.config.get('fluxValidated') and not legacy:
                    p['settings']['image'].update(studio.providers['native-flux'].getRecommendedSettings(),provider='native-flux',model='flux2-klein-4b-q4',workflow='text-to-image')
                if legacy:
                    p['legacyComic']=legacy;p['chapters'][0]['sourceText']=legacy.get('story','');p['chapters'][0]['cleanNarrationText']=clean_narration(legacy.get('story',''))
                    for c in legacy.get('characters',[]):p['characters'].append(character(c.get('name','Character'),c.get('description','')))
                return studio.store.save(p)
            if path=='/studio/import':return studio.store.save(body['project'])
            if path=='/studio/storage-sync':
                if not studio.storage.enabled:raise ValueError('Connect the private Cloudflare R2 bucket before cloud saving.')
                projects=[body['project']] if body.get('project') else [p['id'] for p in studio.store.list_local()]
                for pid in projects:
                    studio.store.load_local(pid);studio.storage.enqueue(pid)
                return studio.storage.status(body.get('project'))
            if path=='/studio/storage-restore':
                if not studio.storage.enabled:raise ValueError('Cloud storage is not connected.')
                return studio.storage.restore(body['project'])
            if path=='/studio/thumbnail':
                from studio_thumbnails import make
                return make(studio.store,body['project'],body.get('options',{}))
            if path=='/studio/publish':
                from studio_publisher import save_upload_snapshot
                operation=body['operation'];options=body.get('options',{})
                if operation=='connect':return studio.publisher.call(operation,{})
                pid=body['project'];project=studio.store.load(pid)
                if operation=='start':
                    result=studio.publisher.call('start',options|{'project':pid})
                    save_upload_snapshot(studio.store,pid,result,initial=True)
                else:
                    if not any(j['id']==options.get('id') for j in project.get('publishing',[])):
                        raise ValueError('This upload does not belong to the selected project.')
                    result=studio.publisher.call(operation,{'id':options['id']})
                    save_upload_snapshot(studio.store,pid,result)
                return result
            if path=='/studio/review':
                from studio_qc import accept_image
                return accept_image(studio.store,body['project'],body.get('chapter'),body['shot'],body)
            if path=='/studio/config':return studio.configure(body)
            if path=='/studio/cloud-test':
                from openai_director import OpenAIDirector
                return OpenAIDirector(studio.config,studio.store.root).verify_key()
            if path=='/studio/cloud-image-connect':
                from qwen_workflow import bundle
                if studio.current:raise ValueError('Finish or cancel the active job before changing image workflows.')
                workflow=Path(__file__).resolve().parent/'runtime'/'qwen-studio-workflows.json'
                workflow.parent.mkdir(exist_ok=True)
                workflow.write_text(json.dumps(bundle(),indent=2),encoding='utf-8')
                health=studio.configure({'comfyWorkflow':str(workflow)})
                provider=studio.providers['comfyui']
                if not health['providers']['comfyui']['installed']:raise ValueError('Cloud workflow unavailable: '+str(health['providers']['comfyui']))
                settings=provider.validateSettings(provider.getRecommendedSettings()|{'model':'qwen-studio-auto'})
                return {'health':health,'settings':settings|{'provider':'comfyui','workflow':'qwen-studio-auto'}}
            if path=='/studio/control':return studio.control(body['action'],body.get('job'),body.get('project'))
            if path=='/studio/jobs':return studio.enqueue(body['project'],body.get('chapter'),body['kind'],body.get('shots'),body.get('options'))
            pid=body['project']
            if path=='/studio/upload':
                from PIL import Image
                raw=base64.b64decode(body['data'].split(',',1)[-1]);img=Image.open(io.BytesIO(raw));img.thumbnail((1536,1536));name='references/'+uid()+'.png';target=studio.store.asset(pid,name);target.parent.mkdir(exist_ok=True);img.convert('RGBA' if body.get('purpose')=='watermark' else 'RGB').save(target,'PNG');return {'path':name}
            def change(p):
                scope=body.get('scope','project');id=body.get('id');action=body.get('action','patch')
                if path=='/studio/edit':
                    if scope=='project':target=p
                    elif scope=='chapter':target=get_chapter(p,id)
                    elif scope=='shot':target=get_shot(p,body['chapter'],id)
                    elif scope=='scene':target=next(s for s in get_chapter(p,body['chapter'])['scenes'] if s['id']==id)
                    elif scope=='character':target=next(c for c in p['characters'] if c['id']==id)
                    elif scope=='person':target=next(c for c in get_chapter(p,body['chapter'])['people'] if c['id']==id)
                    else:raise ValueError('Unknown editor scope.')
                    patch=body['patch']
                    if not isinstance(patch,dict) or any(k in ('id','schemaVersion','revision','created') for k in patch):raise ValueError('Cannot edit stable identity fields.')
                    changed={k:v for k,v in patch.items() if target.get(k)!=v}
                    previous_prompt=target.get('prompt','');previous_handoff=copy.deepcopy(target.get('handoff',{}));previous_video=copy.deepcopy(p['settings'].get('video',{}));previous_watermark=copy.deepcopy(p['settings'].get('watermark',{}));previous_engagement=copy.deepcopy(p['settings'].get('engagement',{}))
                    target.update(patch)
                    if changed and scope in ('shot','scene','chapter'):get_chapter(p,id if scope=='chapter' else body['chapter'])['renderStale']=True;p['renderStale']=True
                    if scope=='project' and 'settings' in changed:
                        from studio_branding import identity as watermark_identity
                        watermark_identity(p,studio.store)
                        if p['settings'].get('video')!=previous_video or p['settings'].get('watermark',{})!=previous_watermark or p['settings'].get('engagement',{})!=previous_engagement:
                            p['renderStale']=True;p['intro']['renderStale']=True
                            for chapter in p['chapters']:chapter['renderStale']=True
                    if scope=='project' and 'intro' in changed:p['renderStale']=True
                    if scope=='chapter' and 'handoff' in patch:
                        from studio_data import warn_dependents
                        warn_dependents(p,target,previous_handoff)
                    if scope in ('shot','scene') and changed:target['origin']='MANUAL';target.setdefault('manual',{}).update({k:True for k in changed})
                    if scope in ('character','person'):
                        if scope=='person' and target.get('type')=='main' and target.get('accepted') and not any(c['id']==id for c in p['characters']):p['characters'].append(copy.deepcopy(target))
                        from image_provider import format_prompt
                        for ch in p['chapters']:
                            for sc in ch['scenes']:
                                for shot in sc['shots']:
                                    for selected in shot['characters']:
                                        if selected['id']==id:selected['type']=target['type']
                                    if target.get('removed'):shot['characters']=[c for c in shot['characters'] if c['id']!=id]
                                    if any(c['id']==id for c in shot['characters']) and not shot.get('manual',{}).get('prompt'):shot['prompt'],shot['negativePrompt']=format_prompt(p,shot,studio.provider(shot['imageProvider']))
                    if scope=='shot' and any(k in changed for k in ('characters','camera','action','lighting','pose','imageProvider','imageModel','workflow')) and ('prompt' not in changed or target['prompt']==previous_prompt):
                        from image_provider import format_prompt
                        if previous_prompt:target.setdefault('promptHistory',[]).append({'prompt':previous_prompt,'time':time.time()})
                        target['prompt'],target['negativePrompt']=format_prompt(p,target,studio.provider(target['imageProvider']))
                        target.get('manual',{}).pop('prompt',None)
                    if scope=='chapter' and ('sourceText' in patch or 'name' in patch) and target.get('narrationMode')!='manual':target['cleanNarrationText']=clean_narration(target['sourceText'],target['name'],target.get('includeChapterLabel',False))
                elif path=='/studio/chapter':
                    if action=='add':p['chapters'].append(new_chapter(len(p['chapters'])+1))
                    else:
                        c=get_chapter(p,id);index=p['chapters'].index(c)
                        if action=='delete':p.setdefault('archivedChapters',[]).append(copy.deepcopy(c));p['chapters'].remove(c)
                        elif action=='duplicate':
                            clone=copy.deepcopy(c);clone['id']=uid('ch-');clone['name']=c['name']+' copy';clone['render']={};clone['status']='READY_FOR_IMAGES' if clone['scenes'] else 'NOT_ANALYZED'
                            for scene in clone['scenes']:
                                scene.update(id=uid('scene-'),chapterId=clone['id'])
                                for shot in scene['shots']:shot.update(id=uid('shot-'),chapterId=clone['id'],sceneId=scene['id'])
                            p['chapters'].insert(index+1,clone)
                        elif action=='move':
                            position=max(0,min(len(p['chapters'])-1,index+int(body['direction'])));p['chapters'].insert(position,p['chapters'].pop(index))
                        elif action=='apply-plan':
                            proposal=c.pop('proposedPlan');c['history'].append({'scenes':c['scenes'],'time':time.time()});c.update(scenes=proposal['scenes'],people=proposal['people'])
                        else:raise ValueError('Unknown chapter operation.')
                    if not p['chapters']:p['chapters'].append(new_chapter(1))
                    for number,c in enumerate(p['chapters'],1):c['number']=number
                elif path=='/studio/character':
                    if action=='add':p['characters'].append(character(body['name'],body.get('description','')))
                    elif action=='promote':
                        c=next(c for c in get_chapter(p,body['chapter'])['people'] if c['id']==id);c['type']='main';c['accepted']=True
                        if not any(x['id']==id for x in p['characters']):p['characters'].append(copy.deepcopy(c))
                    elif action in ('attach','merge'):
                        ch=get_chapter(p,body['chapter']);target=next(x for x in p['characters'] if x['id']==body['target']);source=next(x for x in ch['people'] if x['id']==id);source['attachedTo']=target['id'];source['accepted']=True
                        for scene in ch['scenes']:
                            for shot in scene['shots']:
                                for cast in shot['characters']:
                                    if cast['id']==id:cast.update(id=target['id'],type='main')
                                from image_provider import format_prompt
                                if not shot['manual'].get('prompt'):shot['prompt'],shot['negativePrompt']=format_prompt(p,shot,studio.provider(shot['imageProvider']))
                    elif action=='remove':
                        p['characters']=[c for c in p['characters'] if c['id']!=id]
                        for chapter in p['chapters']:
                            chapter['people']=[c for c in chapter['people'] if c['id']!=id]
                            for scene in chapter['scenes']:
                                scene['characters']=[c for c in scene['characters'] if c['id']!=id]
                                for shot in scene['shots']:
                                    shot['characters']=[c for c in shot['characters'] if c['id']!=id]
                                    from image_provider import format_prompt
                                    if not shot.get('manual',{}).get('prompt'):shot['prompt'],shot['negativePrompt']=format_prompt(p,shot,studio.provider(shot['imageProvider']))
                    else:raise ValueError('Unknown character operation.')
                elif path=='/studio/warning':
                    warning=next(x for x in p['warnings'] if x['id']==id);warning['resolved']=True
                    if action=='update':
                        for ch in p['chapters']:
                            if ch['id'] in warning['affected']:ch['continuityNeedsReview']=True
                else:raise ValueError('Unknown studio endpoint.')
            studio.store.mutate(pid,change)
            if path=='/studio/warning' and body.get('action')=='update':
                latest=studio.store.load(pid);warning=next(x for x in latest['warnings'] if x['id']==body['id'])
                for chapter in latest['chapters']:
                    if chapter['id'] in warning['affected'] and chapter['sourceText'].strip():studio.enqueue(pid,chapter['id'],'analyze')
            from studio_qc import project_view
            return project_view(studio.store,pid)

        def do_POST(self):
            if not self.permitted():
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    self.reply(413, {'error': 'Request is too large.'})
                    return
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Request must be an object.')
                path = self.path
                if path.startswith('/studio/'):
                    self.reply(200,self.studio_post(path,body))
                    return
                # Controls must never wait behind the inference lock.
                if path == '/image/queue':
                    self.reply(202, queue.enqueue(body))
                    return
                if path == '/image/control':
                    self.reply(200, queue.control(body.get('action')))
                    return
                audio_request = path in ('/prepare', '/synthesize')
                if audio_request:
                    queue.control('yield-audio')
                    studio.control('yield-audio')
                if not gpu_lock.acquire(timeout=15 if audio_request else 0):
                    self.reply(409, {'error': 'GPU is generating. Pause the image queue and cancel its current image before switching to Audio.'})
                    return
                try:
                    if path in ('/prepare', '/synthesize'):
                        queue.control('pause')
                        before_audio()
                    if path == '/prepare':
                        voice = body.get('voice')
                        audio_engine.prepare(voice)
                        self.reply(200, audio_engine.health())
                    elif path == '/synthesize':
                        voice = body.get('voice')
                        phonemes, speed = body.get('phonemes'), body.get('speed')
                        if voice not in VOICES:
                            raise ValueError('Choose a supported studio voice.')
                        if not isinstance(phonemes, str) or not 1 <= len(phonemes) <= 278:
                            raise ValueError('Invalid speech section.')
                        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not .5 <= speed <= 2:
                            raise ValueError('Choose a speaking speed between 0.5 and 2.')
                        self.reply(200, audio_engine.synthesize(phonemes, voice, speed), binary=True)
                    elif path == '/image/generate':
                        before_image()
                        self.reply(200, get_image_engine().legacy(body))
                    elif path == '/image/inpaint':
                        before_image()
                        self.reply(200, get_image_engine().legacy({**body, 'operation':'inpaint'}))
                    else:
                        self.reply(404, {'error': 'Unknown endpoint.'})
                finally:
                    gpu_lock.release()
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                self.reply(400, {'error': str(error) or 'Invalid request.'})
            except Exception as error:
                message = str(error)
                if 'out of memory' in message.lower():
                    try:
                        audio_engine.torch.cuda.empty_cache()
                    except Exception:
                        pass
                    message = 'GPU memory ran out. Close GPU apps, use 512×512, or generate without character references.'
                else:
                    print('Local helper request failed:', type(error).__name__, flush=True)
                    traceback.print_exc()
                    message = type(error).__name__ + ': ' + str(error)[:800]
                self.reply(503, {'error': message})
    return Handler


def main():
    print('Loading QuestionTemplate local NVIDIA helper.', flush=True)
    audio_engine = CudaEngine()
    key_path = Path(__file__).resolve().parent / '.pairing-key'
    key = key_path.read_text().strip() if key_path.exists() else secrets.token_hex(32)
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        key = secrets.token_hex(32)
    key_path.write_text(key)
    # Keep the reconnect credential readable only by the current Windows user.
    if os.name == 'nt':
        import subprocess
        account = os.environ.get('USERDOMAIN', '') + '\\' + os.environ.get('USERNAME', '')
        subprocess.run(['icacls', str(key_path), '/inheritance:r', '/grant:r', account + ':F'], capture_output=True)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(audio_engine, key))
    server.daemon_threads = True
    site = 'https://questiontemplate.com/studio.html'
    link = site + '#native=' + key
    print(
        '\nReady on ' + audio_engine.gpu +
        '. Audio and Studio now share this one helper.\n'
        'Keep this window open. Refreshing or switching pages will reconnect automatically.\n'
        'Open this link once to pair:\n' + link,
        flush=True
    )
    if os.environ.get('QT_NO_BROWSER') != '1':
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.RequestHandlerClass.image_queue.close()
        server.RequestHandlerClass.studio_service.close()
        server.server_close()


if __name__ == '__main__':
    main()

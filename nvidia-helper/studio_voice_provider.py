"""Explicit voice selection and private Kaggle notebook transport.

No model downloads on the laptop; existing Kokoro calls keep their old path.
Notebook submission is journaled before dispatch and is never blindly repeated.
"""
import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import wave

VERSION = 2
_KAGGLE_ADMISSION=threading.BoundedSemaphore(1)
NARRATOR = ('Calm, controlled, confident fantasy audiobook narrator. Medium-deep pitch, '
            'clear enunciation, smooth connected delivery, restrained cinematic tension, '
            'natural sentence pauses, subtle emphasis on revelations. No shouting or '
            'promotional delivery. Maintain the same narrator throughout.')
MODELS = {
    'kokoro-82m': {'name':'Kokoro 82M', 'repo':'hexgrad/Kokoro-82M', 'location':'local',
        'license':'Apache-2.0', 'commercial':True, 'instructions':False,
        'note':'Existing fast local voice; no Kaggle account needed.'},
    'qwen3-tts-1.7b': {'name':'Qwen3-TTS 1.7B · CustomVoice',
        'repo':'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice', 'location':'kaggle',
        'license':'Apache-2.0', 'commercial':True, 'instructions':True,
        'note':'Built-in Ryan narrator with delivery instructions and bounded T4 batches.'},
    'dia-1.6b': {'name':'Dia 1.6B', 'repo':'nari-labs/Dia-1.6B-0626', 'location':'kaggle',
        'license':'Apache-2.0', 'commercial':True, 'instructions':False,
        'note':'English dialogue model. Short passages and a generated voice anchor; long-form drift remains unverified.'},
    'fish-s2-pro': {'name':'Fish Audio S2 Pro', 'repo':'fishaudio/s2-pro', 'location':'kaggle',
        'license':'Fish Audio Research License', 'commercial':False, 'instructions':False,
        'note':'Research auditions allowed; monetized narration requires a separate Fish commercial license. Tested workflow uses two T4 GPUs.'},
}

def selected(settings):
    value=settings.get('voiceEngine','kokoro-82m')
    if value not in MODELS: raise ValueError('Choose a supported narration model.')
    return value

def location(settings):
    return 'local' if selected(settings)=='kokoro-82m' and settings.get('voiceCompute','local')=='local' else 'kaggle'

def validate(settings):
    selected(settings)
    if settings.get('voiceCompute','local') not in ('local','kaggle'):
        raise ValueError('Choose local or Kaggle voice compute.')
    instruction=settings.get('voiceInstruction',NARRATOR)
    if not isinstance(instruction,str) or len(instruction)>1200:
        raise ValueError('Narrator instructions must be at most 1200 characters.')
    if type(settings.get('fishCommercialLicense',False)) is not bool:
        raise ValueError('Fish commercial-license confirmation must be on or off.')

def identity(settings):
    """Keep existing Kokoro cache signatures unchanged; remote voices separate."""
    engine=selected(settings)
    if location(settings)=='local':return {}
    return {'voiceEngine':engine,'voiceProviderVersion':VERSION,
            'voiceCompute':'kaggle','voiceInstruction':settings.get('voiceInstruction',NARRATOR)}

def readiness(settings,config,preview=False):
    model=MODELS[selected(settings)]
    if location(settings)=='local':return None
    if not model['commercial'] and not preview and not settings.get('fishCommercialLicense'):
        return 'Fish S2 Pro requires a separate commercial license for production. Use a research audition or another model.'
    reason=KaggleVoice(config).health()['reason'] or None
    if reason:return reason
    if not preview and selected(settings) not in config.get('kaggleVerifiedModels',[]):
        return 'Generate a successful Kaggle audition for this model first. Account verification, GPU fit and audio output have not been verified.'
    return None

def catalogue(config):
    remote=KaggleVoice(config).health()
    return [{'id':key,**model,'ready':key=='kokoro-82m' or key in config.get('kaggleVerifiedModels',[]),
             'status':'Existing local engine' if key=='kokoro-82m' else remote['reason'] or
                 ('Short Kaggle WAV audition passed · long-form quality unverified' if key in config.get('kaggleVerifiedModels',[]) else 'Configured · audition not yet verified'),
             'verified':key=='kokoro-82m' or key in config.get('kaggleVerifiedModels',[])} for key,model in MODELS.items()]

class KaggleVoice:
    def __init__(self,config,clock=time.monotonic,sleep=time.sleep):
        self.config=config;self.clock=clock;self.sleep=sleep

    def _credentials(self):
        path=self.config.get('kaggleKeyFile')
        if not path:return {}
        try:return json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    def health(self):
        creds=self._credentials()
        username=self.config.get('kaggleUsername','')
        installed=bool(importlib.util.find_spec('kaggle'))
        reason=('Connect a private Kaggle API token in Voice settings.' if not creds.get('kaggleApiToken') or not username
                else 'Install the official Kaggle CLI in the helper before using remote voices.' if not installed else '')
        return {'configured':not reason,'username':username,'cliInstalled':installed,'reason':reason,
                'location':'kaggle','quota':'Account GPU quota and notebook stock apply; not an always-on API.',
                'cancelNote':'Cancel stops local publication. An admitted notebook may continue until its bounded timeout.'}

    def _run(self,args,timeout=120):
        env=os.environ.copy();env['KAGGLE_API_TOKEN']=self._credentials().get('kaggleApiToken','')
        config_dir=Path(self.config['kaggleKeyFile']).parent/'.kaggle-voice-cli'
        config_dir.mkdir(exist_ok=True)
        env['KAGGLE_CONFIG_DIR']=str(config_dir)
        env['PYTHONIOENCODING']='utf-8'
        env['PYTHONUTF8']='1'
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        try:
            result=subprocess.run([sys.executable,'-X','utf8','-c','from kaggle.cli import main; main()',*args],env=env,stdin=subprocess.DEVNULL,
                capture_output=True,text=True,encoding='utf-8',timeout=timeout,creationflags=flags)
        except subprocess.TimeoutExpired:
            raise ValueError('Kaggle request timed out. Submission state is uncertain; do not submit a duplicate notebook.') from None
        if result.returncode:
            # Do not emit raw CLI errors, tokens, signed download URLs or story text.
            code='Authentication, phone verification, GPU quota, or notebook setup may need attention.'
            raise ValueError('Kaggle operation failed. '+code)
        return result.stdout

    @staticmethod
    def _write(path,value):
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(path)

    def generate(self,text,target,settings,gate,preview=False,reference=None):
        while not _KAGGLE_ADMISSION.acquire(timeout=.25):gate('Waiting for the shared Kaggle voice slot')
        try:return self._generate(text,target,settings,gate,preview,reference)
        finally:_KAGGLE_ADMISSION.release()

    def _generate(self,text,target,settings,gate,preview=False,reference=None):
        reason=readiness(settings,self.config,preview)
        if reason:raise ValueError(reason)
        username=self.config['kaggleUsername']
        if not re.fullmatch(r'[A-Za-z0-9_]{3,40}',username):raise ValueError('Invalid Kaggle username.')
        engine=selected(settings)
        request={'version':VERSION,'model':engine,'repo':MODELS[engine]['repo'],'text':text,
            'runtimeVersion':11,
            'instruction':settings.get('voiceInstruction',NARRATOR),'seed':81273,'speed':settings.get('speed',1),
            'preview':preview,'effects':settings.get('soundEffects','subtle')}
        request['voice']=settings.get('voice','am_michael')
        if reference:request['reference']=reference
        from studio_data import sentences
        from narration_audio import audio_segments
        request['parts']=[{'text':line,'segments':audio_segments(line,request['effects'])} for line in sentences(text)]
        if engine=='kokoro-82m':
            # Reuse the working phonemizer; Kaggle Python 3.13 cannot build its
            # older English G2P dependencies. Only inference runs on Kaggle.
            from kokoro import KPipeline
            pipeline=KPipeline(lang_code=request['voice'][0],repo_id=request['repo'],model=False)
            phonemes={}
            for part in request['parts']:
                for segment in part['segments']:
                    if segment['kind']!='speech':continue
                    words=segment.get('ttsText',segment['text']).split()
                    for start in range(0,len(words),45):
                        chunk=' '.join(words[start:start+45])
                        phonemes[chunk]=[item.phonemes for item in pipeline(chunk)]
            request['phonemes']=phonemes
        signature=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        journal=target.with_suffix('.kaggle.json')
        folder=target.parent/('.kaggle-'+signature[:16]);folder.mkdir(exist_ok=True)
        slug=f'{username}/studio-voice-{signature[:24]}'
        metadata={'id':slug,'title':'Studio voice '+signature[:24],'code_file':'voice_job.py',
            'language':'python','kernel_type':'script','is_private':True,'enable_gpu':True,
            'enable_internet':True,'dataset_sources':[],'competition_sources':[],'kernel_sources':[]}
        self._write(folder/'kernel-metadata.json',metadata)
        worker=Path(__file__).with_name('studio_kaggle_voice_worker.py').read_text(encoding='utf-8')
        (folder/'voice_job.py').write_text('REQUEST = '+repr(request)+'\n'+worker,encoding='utf-8')
        saved=json.loads(journal.read_text()) if journal.exists() else None
        if saved and saved.get('signature')!=signature:raise ValueError('Kaggle receipt belongs to different narration inputs.')
        if not saved or saved.get('state')=='REJECTED':
            gate('Submitting private Kaggle narration · '+MODELS[engine]['name'])
            # Durable uncertainty marker first: never blindly re-submit a timed-out push.
            saved={'signature':signature,'kernel':slug,'state':'SUBMITTING','created':time.time()}
            self._write(journal,saved)
            pushed=self._run(['kernels','push','-p',str(folder),'--accelerator','NvidiaTeslaT4','--timeout','14400'])
            if 'successfully pushed' not in pushed.lower():
                if 'kernel push error:' in pushed.lower():
                    saved['state']='REJECTED';self._write(journal,saved)
                    raise ValueError('Kaggle rejected notebook admission. Check GPU quota/concurrent sessions, then retry; no generation was confirmed.')
                raise ValueError('Kaggle did not confirm notebook submission. Reconcile this request before retrying; no duplicate was submitted.')
            saved['state']='SUBMITTED';self._write(journal,saved)
        if saved.get('state')=='SUBMITTING':
            raise ValueError('Kaggle submission needs reconciliation in its notebook page. No duplicate was submitted.')
        if not saved.get('privacyVerified'):
            privacy=folder/'privacy';privacy.mkdir(exist_ok=True)
            self._run(['kernels','pull',slug,'-p',str(privacy),'-m'])
            metadata=json.loads((privacy/'kernel-metadata.json').read_text(encoding='utf-8'))
            if metadata.get('is_private') is not True:
                raise ValueError('Kaggle did not confirm private notebook visibility. No audio was published; inspect this notebook before continuing.')
            saved['privacyVerified']=True;self._write(journal,saved)
        deadline=self.clock()+14400
        while True:
            gate('Kaggle voice notebook · waiting for '+MODELS[engine]['name'])
            status=self._run(['kernels','status',slug]).lower()
            if not any(word in status for word in ('complete','running','queued','error','cancel')):
                raise ValueError('Kaggle did not confirm the notebook status. Open its private notebook; no duplicate was submitted.')
            if 'complete' in status:break
            if 'error' in status or 'cancel' in status:
                saved['state']='FAILED';self._write(journal,saved)
                raise ValueError('Kaggle narration failed. Open the private notebook log; previous chapter audio is preserved.')
            if self.clock()>=deadline:raise ValueError('Kaggle narration exceeded the four-hour wait. No duplicate was submitted.')
            self.sleep(8)
        gate('Downloading completed Kaggle narration')
        self._run(['kernels','output',slug,'-p',str(folder),'--file-pattern',r'^(narration\.wav|voice-anchor\.wav|result\.json)$','-q','-o'],timeout=900)
        result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
        if result.get('signature')!=signature or result.get('model')!=engine:
            raise ValueError('Kaggle result does not match the selected narration inputs.')
        source=folder/'narration.wav'
        with wave.open(str(source),'rb') as audio:
            duration=audio.getnframes()/audio.getframerate()
            if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or not math.isfinite(duration) or duration<=0:
                raise ValueError('Kaggle returned an invalid narration WAV.')
        if abs(duration-result.get('duration',0))>.1:raise ValueError('Kaggle audio duration does not match its timing receipt.')
        timings=result.get('sentences',[])
        if not isinstance(timings,list) or len(timings)!=len(request['parts']):raise ValueError('Kaggle returned incomplete narration timing.')
        previous_end=0
        for index,item in enumerate(timings):
            start,end=item.get('start'),item.get('end')
            if (item.get('index')!=index or item.get('text')!=request['parts'][index]['text'] or
                type(start) not in (int,float) or type(end) not in (int,float) or not math.isfinite(start+end) or
                start<previous_end-.01 or end<=start or end>duration+.01):
                raise ValueError('Kaggle narration timing is invalid; earlier audio is preserved.')
            previous_end=end
        gate('Saving selected cloud narration')
        temporary=target.with_suffix('.partial.wav');shutil.copyfile(source,temporary);temporary.replace(target)
        saved['state']='COMPLETE';self._write(journal,saved)
        if result.get('voiceAnchor'):
            source=folder/'voice-anchor.wav'
            with wave.open(str(source),'rb') as anchor:
                if anchor.getnframes()<=0 or anchor.getnchannels()!=1 or anchor.getsampwidth()!=2:
                    raise ValueError('Invalid generated voice reference.')
            name='voice-anchor-'+hashlib.sha256(source.read_bytes()).hexdigest()[:20]+'.wav'
            path=target.parent/name
            if not path.exists():shutil.copyfile(source,path)
            result['voiceAnchor']['path']=str(path)
        # Only output media enters the project's normal verified cloud archive.
        result.update(path=str(target),provider='kaggle',kernel=slug,model=engine,
            timingSource='generated-passage-duration',words=[],paragraphs=[])
        return result

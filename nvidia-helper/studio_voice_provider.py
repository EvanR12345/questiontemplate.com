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

VERSION = 3
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
        'note':'Nine selectable preset speakers with delivery instructions and bounded T4 batches.'},
    'dia-1.6b': {'name':'Dia 1.6B', 'repo':'nari-labs/Dia-1.6B-0626', 'location':'kaggle',
        'license':'Apache-2.0', 'commercial':True, 'instructions':False,
        'note':'English dialogue model. Short passages and a generated voice anchor; long-form drift remains unverified.'},
    'fish-s2-pro': {'name':'Fish Audio S2 Pro', 'repo':'fishaudio/s2-pro', 'location':'kaggle',
        'license':'Fish Audio Research License', 'commercial':False, 'instructions':False,
        'note':'Research auditions allowed; monetized narration requires a separate Fish commercial license. Tested workflow uses two T4 GPUs.'},
}

QWEN_SPEAKERS = ('Ryan','Aiden','Vivian','Serena','Uncle_Fu','Dylan','Eric','Ono_Anna','Sohee')

class KaggleQuotaExhausted(ValueError):
    """Confirmed rejection for exhausted GPU quota, before notebook admission."""

def gpu_quota_exhausted(message):
    # Authentication, stock, concurrency and unknown submissions are not quota
    # exhaustion. Never turn an ambiguous failure into a different narrator.
    return bool(re.search(r'(?:not enough|insufficient)\s+(?:remaining\s+)?gpu\s+(?:time\s+)?quota|gpu\s+(?:time\s+)?quota[^.\n]{0,70}(?:exhausted|exceeded|used up)|(?:exhausted|exceeded)\s+(?:your\s+)?(?:weekly\s+)?gpu\s+(?:time\s+)?quota',str(message),re.I))

def selected(settings):
    value=settings.get('voiceEngine','kokoro-82m')
    if value not in MODELS: raise ValueError('Choose a supported narration model.')
    return value

def anchor_identity(anchor):
    if not anchor:return None
    return (str(anchor['path']).replace('\\','/').rsplit('/',1)[-1],anchor['text'])

def project_anchor(project):
    engine=selected(project['settings'])
    return anchor_identity(project.get('voiceAnchors',{}).get(engine)) if engine in ('dia-1.6b','fish-s2-pro') else None

def location(settings):
    return 'local' if selected(settings)=='kokoro-82m' and settings.get('voiceCompute','local')=='local' else 'kaggle'

def validate(settings):
    selected(settings)
    if settings.get('qwenSpeaker','Ryan') not in QWEN_SPEAKERS:
        raise ValueError('Choose a supported Qwen speaker.')
    if settings.get('voiceCompute','local') not in ('local','kaggle'):
        raise ValueError('Choose local or Kaggle voice compute.')
    batch=settings.get('voiceChapterBatchSize',2)
    if type(batch) is not int or not 1<=batch<=4:raise ValueError('Choose 1–4 chapters per voice job.')
    instruction=settings.get('voiceInstruction',NARRATOR)
    if not isinstance(instruction,str) or len(instruction)>1200:
        raise ValueError('Narrator instructions must be at most 1200 characters.')
    if type(settings.get('fishCommercialLicense',False)) is not bool:
        raise ValueError('Fish commercial-license confirmation must be on or off.')
    if type(settings.get('voiceLocalFallback',False)) is not bool:
        raise ValueError('Local voice fallback must be on or off.')

def identity(settings):
    """Keep existing Kokoro cache signatures unchanged; remote voices separate."""
    engine=selected(settings)
    if location(settings)=='local':return {}
    return {'voiceEngine':engine,'voiceProviderVersion':VERSION,
            'voiceCompute':'kaggle','voiceInstruction':settings.get('voiceInstruction',NARRATOR),
            **({'qwenSpeaker':settings.get('qwenSpeaker','Ryan')} if engine=='qwen3-tts-1.7b' else {})}

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
                 ('Short Kaggle audition passed · long-form quality unverified' if key in config.get('kaggleVerifiedModels',[]) else 'Configured · audition not yet verified'),
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
            if args[:2]==['kernels','push'] and gpu_quota_exhausted(result.stdout+'\n'+result.stderr):
                raise KaggleQuotaExhausted('Kaggle rejected this notebook because GPU quota is exhausted.')
            code='Authentication, phone verification, GPU quota, or notebook setup may need attention.'
            raise ValueError('Kaggle operation failed. '+code)
        return result.stdout

    @staticmethod
    def _write(path,value):
        temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temp.replace(path)

    def generate(self,text,target,settings,gate,preview=False,reference=None,chapter=None):
        while not _KAGGLE_ADMISSION.acquire(timeout=.25):gate('Waiting for the shared Kaggle voice slot')
        try:
            if chapter and not preview:
                target=Path(target)
                cached=self._cached_member(target,chapter)
                if cached:return cached
                item=dict(chapter,text=text,target=str(target))
                recovered=self._recover_groups(target.parent.parent/'.voice-groups',{item['id']:item},gate)
                if recovered:return recovered[item['id']]
            result=self._generate(text,target,settings,gate,preview,reference,
                                  chapter_id=chapter['id'] if chapter and not preview else None)
            if chapter and not preview:self._write_chapter(target,chapter['signature'],result)
            return result
        finally:_KAGGLE_ADMISSION.release()

    def generate_many(self,items,settings,gate,reference=None):
        """One private notebook/model load, separate chapter Opus assets.

        Only bounded same-model groups; no chapter labels enter the audio.
        Receipts publish independently so an interrupted split can recover.
        """
        if not 1<=len(items)<=4 or len({item['id'] for item in items})!=len(items):
            raise ValueError('Narration groups need 1–4 distinct chapters.')
        if sum(len(item['text'].split()) for item in items)>4000:
            raise ValueError('Keep one voice group under 4000 words.')
        while not _KAGGLE_ADMISSION.acquire(timeout=.25):gate('Waiting for the shared Kaggle voice slot')
        try:
            folder=Path(items[0]['target']).parent.parent/'.voice-groups'
            folder.mkdir(exist_ok=True)
            results={}
            for item in items:
                target=Path(item['target'])
                result=self._cached_member(target,item)
                old_single=target.with_name(target.name+'.kaggle.json')
                if not result and old_single.is_file() and json.loads(old_single.read_text())['state']!='REJECTED':
                    result=self._generate(item['text'],target,settings,gate,reference=reference,chapter_id=item['id'])
                    self._write_chapter(target,item['signature'],result)
                if result:results[item['id']]=result
            missing={i['id']:i for i in items if i['id'] not in results}

            # Older helpers journaled the complete group but published its member
            # receipts independently. Reattach an intact group recipe on upgrade.
            if missing and len(missing)<len(items):
                group=self._group_request(folder,items,settings,reference)
                path=folder/(group['group']+'.publication.json')
                journal=folder/(group['group']+'.opus.kaggle.json')
                if not path.is_file() and journal.is_file():
                    saved=json.loads(journal.read_text(encoding='utf-8'))
                    if saved.get('state')!='REJECTED':self._write(path,group)

            # Only interrupted groups leave these records. Submitted notebooks keep their
            # original identity, even when a caller now asks for just one of their chapters.
            results.update(self._recover_groups(folder,missing,gate))
            if missing:
                group=self._group_request(folder,list(missing.values()),settings,reference)
                path=folder/(group['group']+'.publication.json')
                self._write(path,group)
                results.update(self._publish_group(path,group,missing,gate))
            return [results[i['id']] for i in items]
        finally:_KAGGLE_ADMISSION.release()

    def _recover_groups(self,folder,missing,gate):
        results={}
        for path in sorted(folder.glob('*.publication.json')):
            if not missing:break
            group=json.loads(path.read_text(encoding='utf-8'))
            if 'outputs' not in group:
                journal=folder/(group['group']+'.opus.kaggle.json')
                if not journal.is_file():continue
                saved=json.loads(journal.read_text(encoding='utf-8'))
                if saved.get('state')=='REJECTED':continue
            recovered=self._publish_group(path,group,missing,gate)
            results.update(recovered)
            for key in recovered:del missing[key]
        return results

    @staticmethod
    def _group_request(folder,items,settings,reference):
        fingerprint=hashlib.sha256(json.dumps([(i['id'],i['signature']) for i in items],sort_keys=True).encode()).hexdigest()
        keys=('voiceEngine','voiceCompute','voice','qwenSpeaker','voiceInstruction','speed',
              'soundEffects','fishCommercialLicense')
        return {'group':fingerprint,'settings':{k:settings[k] for k in keys if k in settings},
                'reference':reference,'items':[dict(i,target=str(Path(i['target']).relative_to(folder.parent))) for i in items]}

    def _publish_group(self,path,group,missing,gate):
        folder=path.parent
        wanted=[]
        for item in group['items']:
            current=missing.get(item['id'])
            if not current:continue
            target=Path(current['target'])
            if item['signature']==current['signature']:
                wanted.append((item,target))
            elif (item['signature']==current.get('baseSignature') and group.get('anchor')
                    and anchor_identity(group['outputs'][item['id']]['result'].get('voiceAnchor'))==tuple(current['anchor'])):
                # The first published chapter may already have selected this
                # group's generated anchor. Keep the original receipt identity.
                wanted.append((item,target.with_name(current['baseName'])))
        if not wanted:return {}
        if 'outputs' not in group:
            self._stage_group(path,group,gate)
        anchor_path=None
        if group.get('anchor'):
            anchor=group['anchor']
            anchor_path=folder.parent/Path(anchor['source']).name
            self._publish_asset(folder,anchor,anchor_path)
        results={}
        for item,target in wanted:
            gate('Saving completed chapter narration from the shared voice job')
            output=group['outputs'][item['id']]
            self._publish_asset(folder,output,target)
            result=dict(output['result'],path=str(target))
            if anchor_path:
                result['voiceAnchor']=dict(result['voiceAnchor'],path=str(anchor_path))
            self._write(target.with_suffix('.voice-ready.json'),{'inputSignature':item['signature'],
                'sha256':output['sha256'],'result':result})
            results[item['id']]=result

        # Keep the shared receipt if this call requested only some outstanding chapters.
        if len(wanted)==len(group['items']) or all(self._saved_chapter(folder.parent/i['target'],
                i['signature'],group['outputs'][i['id']]['sha256']) for i in group['items']):
            path.unlink(missing_ok=True)
        return results

    @classmethod
    def _publish_asset(cls,folder,output,target):
        if target.is_file() and cls._checksum(target)==output['sha256']:return
        source=folder/output['source']
        if not source.resolve().is_relative_to(folder.resolve()) or not source.is_file():
            raise ValueError('Completed Kaggle audio is unavailable. Restore the saved group before retrying.')
        target.parent.mkdir(exist_ok=True)
        temporary=target.with_name(target.name+'.voice-partial')
        try:
            shutil.copyfile(source,temporary)
            if cls._checksum(temporary)!=output['sha256']:
                raise ValueError('Completed Kaggle audio failed its checksum. Restore the saved group before retrying.')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _checksum(path):
        value=hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):value.update(block)
        return value.hexdigest()

    def _write_chapter(self,target,signature,result):
        self._write(target.with_suffix('.voice-ready.json'),{'inputSignature':signature,
                    'sha256':self._checksum(target),'result':result})

    @classmethod
    def _saved_chapter(cls,target,signature,expected_hash=None):
        receipt=target.with_suffix('.voice-ready.json')
        if not receipt.is_file() or not target.is_file():return None
        value=json.loads(receipt.read_text(encoding='utf-8'))
        checksum=value.get('sha256')
        if (value.get('inputSignature')!=signature or (expected_hash and checksum!=expected_hash)
                or cls._checksum(target)!=checksum):return None
        return dict(value['result'],path=str(target))

    @classmethod
    def _cached_member(cls,target,item):
        result=cls._saved_chapter(target,item['signature'])
        if result or not item.get('baseName'):return result
        previous=cls._saved_chapter(target.with_name(item['baseName']),item['baseSignature'])
        if previous and anchor_identity(previous.get('voiceAnchor'))==tuple(item['anchor']):
            return previous
        return None

    def _stage_group(self,path,group,gate):
        """Validate and index every completed output together, before publishing any."""
        from studio_data import sentences
        from narration_audio import audio_segments
        folder=path.parent
        items=group['items']
        settings=group['settings']
        parts=[];ranges=[]
        for item in items:
            start=len(parts)
            parts.extend({'text':line,'chapterId':item['id'],'segments':audio_segments(line,settings.get('soundEffects','subtle'))}
                for line in sentences(item['text']))
            if len(parts)==start:raise ValueError('Chapter narration is empty.')
            ranges.append((start,len(parts)))
        result=self._generate('\n'.join(i['text'] for i in items),folder/(group['group']+'.opus'),settings,gate,
            reference=group['reference'],parts_override=parts)
        if result.get('voiceAnchor'):
            source=Path(result['voiceAnchor']['path'])
            group['anchor']={'source':str(source.relative_to(folder)),'sha256':self._checksum(source)}
            result['voiceAnchor']=dict(result['voiceAnchor'],path=source.name)
        chapter_audio=result.get('chapterAudio',[])
        if len(chapter_audio)!=len(items):
            raise ValueError('Kaggle did not return every separate chapter Opus.')
        outputs={}
        for item,(first,last),asset in zip(items,ranges,chapter_audio):
            timings=result['sentences'][first:last]
            begin=timings[0]['start'];end=timings[-1]['end']
            chapter_result={k:v for k,v in result.items() if k not in ('path','chapterAudio','setupAndModelLoadSeconds','speechGenerationSeconds','setupAndGenerationSeconds')} | {
                'duration':end-begin,
                'sentences':[dict(t,index=index,start=max(0,t['start']-begin),end=t['end']-begin)
                    for index,t in enumerate(timings)],
                'voiceBatch':{'chapters':len(items),'group':group['group'],'setupShared':True,
                    'groupSetupSeconds':result.get('setupAndModelLoadSeconds'),
                    'groupSpeechSeconds':result.get('speechGenerationSeconds')},
                'timingAllocation':'Shared notebook measurements; not isolated chapter throughput'}
            source=Path(asset['path'])
            outputs[item['id']]={'source':str(source.relative_to(folder)),'sha256':self._checksum(source),'result':chapter_result}
        group['outputs']=outputs
        # Keep only identities and relative paths once the original request can be recovered locally.
        del group['settings'],group['reference']
        for item in items:del item['text']
        self._write(path,group)

    def _generate(self,text,target,settings,gate,preview=False,reference=None,parts_override=None,chapter_id=None):
        validate(settings)
        reason=readiness(settings,self.config,preview)
        if reason:raise ValueError(reason)
        username=self.config['kaggleUsername']
        if not re.fullmatch(r'[A-Za-z0-9_]{3,40}',username):raise ValueError('Invalid Kaggle username.')
        engine=selected(settings)
        request={'version':VERSION,'model':engine,'repo':MODELS[engine]['repo'],'text':text,
            'runtimeVersion':14,'qwenSpeaker':settings.get('qwenSpeaker','Ryan'),
            'instruction':settings.get('voiceInstruction',NARRATOR),'seed':81273,'speed':settings.get('speed',1),
            'preview':preview,'effects':settings.get('soundEffects','subtle')}
        request['voice']=settings.get('voice','am_michael')
        if reference:request['reference']=reference
        from studio_data import sentences
        from narration_audio import audio_segments
        request['parts']=[{'text':line,'segments':audio_segments(line,request['effects'])} for line in sentences(text)]
        if parts_override is not None:
            request['parts']=parts_override
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
        journal=target.with_name(target.name+'.kaggle.json')
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
        if saved and saved.get('kernel') and saved['kernel']!=slug:
            raise ValueError('Reconnect the original private Kaggle account to recover this notebook.')
        if not saved or saved.get('state')=='REJECTED':
            from studio_voice_legacy import overlapping_group
            group_folder=target.parent.parent/'.voice-groups' if chapter_id else target.parent
            owner=overlapping_group(group_folder,request,chapter_id,journal)
            if owner:
                if owner[1].get('state')=='SUBMITTING':
                    raise ValueError('Kaggle submission needs reconciliation in its original private notebook. No duplicate was submitted.')
                count=len({part['chapterId'] for part in owner[2]['parts']})
                raise ValueError(f'Resume the original {count}-chapter Kaggle group to collect its audio. No duplicate was submitted.')
            gate('Submitting private Kaggle narration · '+MODELS[engine]['name'])
            # Durable uncertainty marker first: never blindly re-submit a timed-out push.
            saved={'signature':signature,'kernel':slug,'state':'SUBMITTING','created':time.time()}
            if parts_override is not None:
                saved['chapterIds']=list(dict.fromkeys(part['chapterId'] for part in request['parts']))
            self._write(journal,saved)
            try:
                pushed=self._run(['kernels','push','-p',str(folder),'--accelerator','NvidiaTeslaT4','--timeout','14400'])
            except KaggleQuotaExhausted:
                saved['state']='REJECTED';saved['reason']='gpu-quota-exhausted';self._write(journal,saved)
                raise
            if 'successfully pushed' not in pushed.lower():
                if 'kernel push error:' in pushed.lower():
                    saved['state']='REJECTED';self._write(journal,saved)
                    if gpu_quota_exhausted(pushed):
                        raise KaggleQuotaExhausted('Kaggle rejected this notebook because GPU quota is exhausted.')
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
        self._run(['kernels','output',slug,'-p',str(folder),'--file-pattern',r'^(narration\.opus|chapter-[0-9]{3}\.opus|voice-anchor\.opus|result\.json)$','-q','-o'],timeout=900)
        result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
        if result.get('signature')!=signature or result.get('model')!=engine:
            raise ValueError('Kaggle result does not match the selected narration inputs.')
        from studio_audio import probe
        duration=result.get('duration',0)
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0:
            raise ValueError('Kaggle returned an invalid narration duration.')
        def check_audio(source,expected):
            info=probe(source,self.config.get('ffmpeg'))
            if info['codec']!='opus' or info['channels']!=1 or abs(info['duration']-expected)>.1:
                raise ValueError('Kaggle Opus audio does not match its timing receipt.')
        if parts_override is None:
            source=folder/'narration.opus'
            check_audio(source,duration)
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
        if parts_override is None:
            temporary=target.with_suffix('.partial.opus');shutil.copyfile(source,temporary);temporary.replace(target)
        else:
            groups=[]
            for index,part in enumerate(request['parts']):
                if not groups or groups[-1]['id']!=part['chapterId']:
                    groups.append({'id':part['chapterId'],'first':index,'last':index+1})
                else:groups[-1]['last']=index+1
            assets=result.get('chapterAudio',[])
            if not isinstance(assets,list) or len(assets)!=len(groups):
                raise ValueError('Kaggle did not return every separate chapter Opus.')
            for index,(group,asset) in enumerate(zip(groups,assets)):
                filename=f'chapter-{index:03d}.opus'
                if any(asset.get(k)!=group[k] for k in ('id','first','last')) or asset.get('path')!=filename:
                    raise ValueError('Kaggle chapter audio does not match the requested chapters.')
                expected=timings[group['last']-1]['end']-timings[group['first']]['start']
                check_audio(folder/filename,expected)
                asset['path']=str(folder/filename)
        if result.get('voiceAnchor'):
            source=folder/'voice-anchor.opus'
            info=probe(source,self.config.get('ffmpeg'))
            if info['codec']!='opus' or info['channels']!=1:
                raise ValueError('Invalid generated Opus voice reference.')
            checksum=self._checksum(source)
            path=target.parent/('voice-anchor-'+checksum[:20]+'.opus')
            self._publish_asset(target.parent,{'source':str(source.relative_to(target.parent)),'sha256':checksum},path)
            result['voiceAnchor']['path']=str(path)
        saved['state']='COMPLETE';self._write(journal,saved)
        # Only output media enters the project's normal verified cloud archive.
        result.update(path=str(target),provider='kaggle',kernel=slug,model=engine,qwenSpeaker=request.get('qwenSpeaker') if engine=='qwen3-tts-1.7b' else None,
            timingSource='generated-passage-duration',words=[],paragraphs=[],audioCodec='opus',audioBitrate=128000)
        return result

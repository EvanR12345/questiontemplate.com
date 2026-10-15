"""Portable private Kaggle script. REQUEST is inserted by the trusted helper.

Only the selected model is downloaded to ephemeral scratch space. Existing Kaggle
Torch is retained; this never touches the laptop's shared CUDA environment.
"""
import base64
import hashlib
import shutil
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

FISH_COMMIT='541566932abbaebcb0a40e562a1f846738383f38'
DIA_COMMIT='876125e461a03b157ec905b0fe8b57a0f8b9e7a0'
_FISH_SERVER=None

def install(packages,no_deps=False,ignore_python=False):
    args=[sys.executable,'-m','pip','install','--quiet','--disable-pip-version-check','--no-cache-dir']
    if no_deps:args+=['--no-deps']
    if ignore_python:args+=['--ignore-requires-python']
    args+=['-c','/kaggle/tmp/studio-voice/constraints.txt',*packages]
    subprocess.run(args,check=True,stdout=subprocess.DEVNULL)

def chunks(text,max_words=45):
    words=text.split()
    return [' '.join(words[i:i+max_words]) for i in range(0,len(words),max_words)]

def run(request):
    global _FISH_SERVER
    scratch=Path('/kaggle/tmp/studio-voice');scratch.mkdir(parents=True,exist_ok=True)
    if os.environ.get('STUDIO_VOICE_ISOLATED')!='1':
        # Reuse Kaggle's Torch/CUDA through system-site-packages. Model-specific
        # Python packages cannot downgrade Kaggle's own Google/API environment.
        runtime=scratch/'runtime'
        subprocess.run([sys.executable,'-m','venv','--without-pip','--system-site-packages',str(runtime)],check=True)
        env=os.environ.copy();env['STUDIO_VOICE_ISOLATED']='1'
        subprocess.run([str(runtime/'bin/python'),str(Path(__file__).resolve())],env=env,check=True)
        return
    os.environ['HF_HOME']=str(scratch/'models');os.environ['PIP_CACHE_DIR']=str(scratch/'pip')
    import torch
    if not torch.cuda.is_available():raise RuntimeError('Enable a Kaggle GPU accelerator before running this notebook.')
    # Pip must not replace the preinstalled multi-GB CUDA stack.
    (scratch/'constraints.txt').write_text('torch==='+torch.__version__+'\n')
    original_torch=torch.__version__
    install(['soundfile','huggingface-hub','einops','librosa'])
    model=request['model'];torch.manual_seed(request['seed'])
    import numpy as np
    import soundfile as sf
    reference=request.get('reference')
    reference_path=None
    if reference:
        reference_path=scratch/'saved-narrator.wav'
        reference_path.write_bytes(base64.b64decode(reference['audio'],validate=True))
    if model=='kokoro-82m':
        # Kokoro's pure-Python wheel declares <3.13. Kaggle now uses 3.13;
        # this path stays audition-gated until the actual runtime passes.
        install(['kokoro==0.9.4'],no_deps=True,ignore_python=True)
        install(['loguru','munch','transformers==4.57.3'])
        # Import only the neural model. The package eagerly imports KPipeline,
        # whose unused English G2P dependencies do not build on Python 3.13.
        import importlib.util
        package=Path(importlib.util.find_spec('kokoro').origin)
        init=package.read_text()
        if init.count('from .pipeline import KPipeline')!=1:
            raise RuntimeError('Kokoro pinned import layout changed; refusing an unsafe patch.')
        package.write_text(init.replace('from .pipeline import KPipeline','# Phonemes supplied by the existing helper.'))
        from kokoro import KModel
        from huggingface_hub import hf_hub_download
        voice=request['voice']
        engine=KModel(repo_id=request['repo']).to('cuda:0').eval()
        voice_tensor=torch.load(hf_hub_download(request['repo'],filename='voices/'+voice+'.pt'),
                                map_location='cpu',weights_only=True).to('cuda:0')
        def speak(text):
            audio=[]
            for phonemes in request['phonemes'][text]:
                phonemes=''.join(c for c in phonemes if c in engine.vocab)
                while phonemes:
                    end=phonemes.rfind(' ',0,251) if len(phonemes)>250 else len(phonemes)
                    if end<=0:end=min(250,len(phonemes))
                    section=phonemes[:end].strip();phonemes=phonemes[end:].lstrip()
                    if not section:continue
                    with torch.inference_mode():piece=engine(section,voice_tensor[len(section)-1],1)
                    audio.append(piece.float().cpu().numpy())
            return np.concatenate(audio),24000
        word_limit=45
    elif model=='qwen3-tts-1.7b':
        install(['qwen-tts==0.1.1'],no_deps=True)
        install(['transformers==4.57.3','accelerate==1.12.0','sox','onnxruntime'])
        from qwen_tts import Qwen3TTSModel
        engine=Qwen3TTSModel.from_pretrained(request['repo'],device_map='cuda:0',dtype=torch.float16,
                                           attn_implementation='sdpa')
        def speak(text):
            wavs,sr=engine.generate_custom_voice(text=text,language='English',speaker=request.get('qwenSpeaker','Ryan'),
                instruct=request['instruction'],max_new_tokens=4096)
            return np.asarray(wavs[0],dtype=np.float32),sr
        word_limit=45
    elif model=='dia-1.6b':
        install(['git+https://github.com/nari-labs/dia.git@'+DIA_COMMIT],no_deps=True)
        install(['descript-audio-codec','pydantic>=2.11.3','safetensors'])
        from dia.model import Dia
        engine=Dia.from_pretrained(request['repo'],compute_dtype='float32')
        anchor=reference_path;anchor_text=reference['text'] if reference else None
        def speak(text):
            nonlocal anchor,anchor_text
            torch.manual_seed(request['seed'])
            prompt='[S1] '+text
            kwargs={}
            if anchor:
                prompt='[S1] '+anchor_text+' '+text
                kwargs['audio_prompt']=str(anchor)
            audio=engine.generate(prompt+' [S1]',use_torch_compile=False,verbose=False,
                cfg_scale=4.0,temperature=1.8,top_p=.9,cfg_filter_top_k=50,**kwargs)
            if anchor is None:
                anchor=scratch/'narrator-anchor.wav';anchor_text=text
                sf.write(anchor,audio,44100)
            return np.asarray(audio,dtype=np.float32),44100
        word_limit=25
    elif model=='fish-s2-pro':
        if torch.cuda.device_count()<2:
            raise RuntimeError('Fish S2 Pro requires Kaggle T4 x2 here: speech model on GPU 0, codec on GPU 1.')
        install(['git+https://github.com/fishaudio/fish-speech.git@'+FISH_COMMIT],no_deps=True)
        install(['transformers==5.19.0','hydra-core','natsort','kui','uvicorn','loguru','pyrootutils',
            'resampy','einx==0.2.2','pydub','ormsgpack','tiktoken','pydantic==2.9.2','cachetools',
            'descript-audio-codec','safetensors','opencc-python-reimplemented','modelscope==1.17.1','silero-vad'])
        from huggingface_hub import snapshot_download
        checkpoint=snapshot_download(request['repo'],local_dir=str(scratch/'s2-pro'))
        # Use the official server/client protocol, bound to loopback inside Kaggle.
        repo=scratch/'fish-source'
        subprocess.run(['git','clone','--quiet','https://github.com/fishaudio/fish-speech.git',str(repo)],check=True)
        subprocess.run(['git','-C',str(repo),'checkout','--quiet',FISH_COMMIT],check=True)
        # The official S2 server loads both networks on one device. Its codec
        # decoder already routes codes to decoder_model.device. Separate it onto
        # the second T4 to avoid the measured checkpoint-loading OOM; weights
        # and precision are unchanged.
        manager=repo/'tools/server/model_manager.py'
        source=manager.read_text()
        original='decoder_config_name, decoder_checkpoint_path, self.device'
        if source.count(original)!=1:raise RuntimeError('Fish pinned loader changed; refusing an unsafe device patch.')
        manager.write_text(source.replace(original,
            'decoder_config_name, decoder_checkpoint_path, "cuda:1" if self.model_family == "s2" else self.device'))
        inference_file=repo/'fish_speech/inference_engine/__init__.py'
        source=inference_file.read_text()
        original='device=self.decoder_model.device,\n            text=req.text'
        if source.count(original)!=1:raise RuntimeError('Fish pinned request router changed; refusing an unsafe device patch.')
        inference_file.write_text(source.replace(original,
            'device=torch.device("cuda:0") if self.model_family == "s2" else self.decoder_model.device,\n            text=req.text'))
        server=subprocess.Popen([sys.executable,str(repo/'tools/api_server.py'),'--listen','127.0.0.1:8080',
            '--llama-checkpoint-path',checkpoint,'--decoder-checkpoint-path',checkpoint+'/codec.pth',
            '--decoder-config-name','modded_dac_vq','--half'],cwd=repo,stdout=subprocess.DEVNULL)
        _FISH_SERVER=server
        import urllib.request
        for _ in range(180):
            if server.poll() is not None:raise RuntimeError('Fish model failed to load; inspect Kaggle GPU memory and runtime.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8080/v1/health',timeout=3) as response:
                    if response.status==200:break
            except OSError:time.sleep(2)
        else:raise RuntimeError('Fish model did not become ready within six minutes.')
        anchor=reference_path;anchor_text=reference['text'] if reference else None
        def speak(text):
            nonlocal anchor,anchor_text
            import io,ormsgpack,requests
            from fish_speech.utils.schema import ServeTTSRequest,ServeReferenceAudio
            refs=[ServeReferenceAudio(audio=anchor.read_bytes(),text=anchor_text)] if anchor else []
            payload=ServeTTSRequest(text=text,references=refs,format='wav',streaming=False,
                seed=request['seed'],chunk_length=200,use_memory_cache='on')
            response=requests.post('http://127.0.0.1:8080/v1/tts',params={'format':'msgpack'},
                data=ormsgpack.packb(payload.model_dump(exclude_none=True)),
                headers={'content-type':'application/msgpack'},timeout=600)
            response.raise_for_status()
            audio,sr=sf.read(io.BytesIO(response.content),dtype='float32')
            if anchor is None:
                anchor=scratch/'narrator-anchor.wav';anchor_text=text;sf.write(anchor,audio,sr)
            return np.asarray(audio),sr
        word_limit=25
    else:raise RuntimeError('Unsupported Kaggle voice model.')
    if torch.__version__!=original_torch:raise RuntimeError('The Kaggle Torch stack changed unexpectedly.')
    model_ready=time.monotonic()
    from scipy.signal import resample_poly
    from math import gcd
    pieces=[];timings=[];offset=0.;sample_rate=24000
    prepared={}
    qwen_audio={}
    if model=='qwen3-tts-1.7b':
        texts=[]
        for part in request['parts']:
            for segment in part['segments']:
                if segment['kind']=='speech':texts.extend(chunks(segment.get('ttsText',segment['text']),word_limit))
        # The published Qwen API supports batched custom voices. Keep batches
        # bounded for T4 memory; each output retains its real generated duration.
        for start in range(0,len(texts),4):
            batch=texts[start:start+4]
            wavs,sr=engine.generate_custom_voice(text=batch,language=['English']*len(batch),
                speaker=[request.get('qwenSpeaker','Ryan')]*len(batch),instruct=[request['instruction']]*len(batch),max_new_tokens=4096)
            for text,audio in zip(batch,wavs):qwen_audio[text]=(np.asarray(audio,dtype=np.float32),sr)
    if model=='dia-1.6b':
        # Dia's documented useful passage length is roughly 5–20 seconds.
        # Group clean adjacent sentences instead of feeding tiny fragments.
        index=0
        while index<len(request['parts']):
            group=[];words=0
            while index<len(request['parts']):
                part=request['parts'][index]
                if len(part['segments'])!=1 or part['segments'][0]['kind']!='speech':break
                phrase=part['segments'][0].get('ttsText',part['text']);count=len(phrase.split())
                if group and words+count>45:break
                group.append((index,phrase));words+=count;index+=1
            if not group:index+=1;continue
            audio,sr=speak(' '.join(text for _,text in group))
            lengths=[len(text.split()) for _,text in group];cursor=0
            for j,(part_index,_) in enumerate(group):
                end=len(audio) if j==len(group)-1 else round(len(audio)*sum(lengths[:j+1])/sum(lengths))
                prepared[part_index]=(audio[cursor:end],sr);cursor=end
    for index,part in enumerate(request['parts']):
        start=offset
        for segment in part['segments']:
            if segment['kind']=='speech':
                for phrase in ([segment.get('ttsText',segment['text'])] if index in prepared
                               else chunks(segment.get('ttsText',segment['text']),word_limit)):
                    audio,sr=(prepared[index] if index in prepared else qwen_audio[phrase]
                              if model=='qwen3-tts-1.7b' else speak(phrase))
                    if audio.ndim>1:audio=audio.mean(axis=1)
                    if not len(audio) or not np.isfinite(audio).all():raise RuntimeError('Voice model returned empty or non-finite audio.')
                    common=gcd(int(sr),sample_rate);audio=resample_poly(audio,sample_rate//common,int(sr)//common)
                    if request['speed']!=1:
                        import librosa
                        audio=librosa.effects.time_stretch(audio,rate=request['speed'])
                    fade=min(120,len(audio)//2);audio[:fade]*=np.linspace(0,1,fade);audio[-fade:]*=np.linspace(1,0,fade)
                    pieces.append(audio);offset+=len(audio)/sample_rate
            else:
                # Sound-effect cues never become spoken letters. Remote audition
                # uses a deliberate brief pause; local Kokoro retains existing SFX.
                length=round(float(segment.get('duration',.18))*sample_rate)
                pieces.append(np.zeros(length,dtype=np.float32));offset+=length/sample_rate
        if offset==start:
            pieces.append(np.zeros(2400,dtype=np.float32));offset+=.1
        timings.append({'index':index,'text':part['text'],'start':start,'end':offset,
                        'timingSource':'estimated-within-generated-passage' if index in prepared else 'generated-passage-duration'})
        print('Completed narration passage',index+1,'/',len(request['parts']),flush=True)
    audio=np.concatenate(pieces) if pieces else np.empty(0)
    if not len(audio):raise RuntimeError('No spoken narration was generated.')
    if float(np.max(np.abs(audio)))<.0001:raise RuntimeError('Voice model returned silent audio.')
    sf.write('/kaggle/working/narration.wav',np.clip(audio,-.99,.99),sample_rate,subtype='PCM_16')
    result={'signature':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),
        'model':model,'duration':len(audio)/sample_rate,'sampleRate':sample_rate,'sentences':timings,
        'provider':'kaggle','voiceConsistency':'generated-anchor' if model in ('dia-1.6b','fish-s2-pro') else 'built-in preset',
        'soundEffects':'pauses','setupAndGenerationSeconds':time.monotonic()-started}
    result.update(setupAndModelLoadSeconds=model_ready-started,
        speechGenerationSeconds=time.monotonic()-model_ready,
        hardware={'gpuCount':torch.cuda.device_count(),'gpu':torch.cuda.get_device_name(0),
                  'torch':torch.__version__,'python':sys.version.split()[0]})
    if model in ('dia-1.6b','fish-s2-pro') and anchor is not None:
        anchor_audio,anchor_sr=sf.read(anchor,dtype='float32')
        sf.write('/kaggle/working/voice-anchor.wav',anchor_audio,anchor_sr,subtype='PCM_16')
        result['voiceAnchor']={'text':anchor_text,'generated':True}
    Path('/kaggle/working/result.json').write_text(json.dumps(result),encoding='utf-8')

if __name__=='__main__':
    started=time.monotonic()
    try:run(REQUEST)
    finally:
        if _FISH_SERVER is not None and _FISH_SERVER.poll() is None:
            _FISH_SERVER.terminate()
            try:_FISH_SERVER.wait(timeout=20)
            except subprocess.TimeoutExpired:_FISH_SERVER.kill()

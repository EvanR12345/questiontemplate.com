"""Portable private Kaggle script. REQUEST is inserted by the trusted helper.

Only the selected model is downloaded to ephemeral scratch space. Existing Kaggle
Torch is retained; this never touches the laptop's shared CUDA environment.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

FISH_COMMIT='541566932abbaebcb0a40e562a1f846738383f38'
DIA_COMMIT='876125e461a03b157ec905b0fe8b57a0f8b9e7a0'

def install(packages,no_deps=False):
    args=[sys.executable,'-m','pip','install','--quiet','--disable-pip-version-check','--no-cache-dir']
    if no_deps:args+=['--no-deps']
    args+=['-c','/kaggle/tmp/studio-voice/constraints.txt',*packages]
    subprocess.run(args,check=True,stdout=subprocess.DEVNULL)

def chunks(text,max_words=45):
    words=text.split()
    return [' '.join(words[i:i+max_words]) for i in range(0,len(words),max_words)]

def run(request):
    scratch=Path('/kaggle/tmp/studio-voice');scratch.mkdir(parents=True,exist_ok=True)
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
    if model=='kokoro-82m':
        install(['kokoro==0.9.4'],no_deps=True)
        install(['misaki[en]','loguru','phonemizer'])
        from kokoro import KPipeline
        voice=request['voice']
        engine=KPipeline(lang_code=voice[0],repo_id=request['repo'],device='cuda')
        def speak(text):
            audio=[item.audio.detach().cpu().numpy() if hasattr(item.audio,'detach') else np.asarray(item.audio)
                   for item in engine(text,voice=voice,speed=1)]
            return np.concatenate(audio),24000
        word_limit=45
    elif model=='qwen3-tts-1.7b':
        install(['qwen-tts==0.1.1'],no_deps=True)
        install(['transformers==4.57.3','accelerate==1.12.0','sox','onnxruntime'])
        from qwen_tts import Qwen3TTSModel
        engine=Qwen3TTSModel.from_pretrained(request['repo'],device_map='cuda:0',dtype=torch.float16,
                                           attn_implementation='sdpa')
        def speak(text):
            wavs,sr=engine.generate_custom_voice(text=text,language='English',speaker='Ryan',
                instruct=request['instruction'],max_new_tokens=4096)
            return np.asarray(wavs[0],dtype=np.float32),sr
        word_limit=45
    elif model=='dia-1.6b':
        install(['git+https://github.com/nari-labs/dia.git@'+DIA_COMMIT],no_deps=True)
        install(['descript-audio-codec','pydantic>=2.11.3','safetensors'])
        from dia.model import Dia
        engine=Dia.from_pretrained(request['repo'],compute_dtype='float16')
        anchor=None;anchor_text=None
        def speak(text):
            nonlocal anchor,anchor_text
            torch.manual_seed(request['seed'])
            prompt='[S1] '+text
            kwargs={}
            if anchor:
                prompt='[S1] '+anchor_text+' '+text
                kwargs['audio_prompt']=str(anchor)
            audio=engine.generate(prompt,use_torch_compile=False,verbose=False,**kwargs)
            if anchor is None:
                anchor=scratch/'narrator-anchor.wav';anchor_text=text
                sf.write(anchor,audio,44100)
            return np.asarray(audio,dtype=np.float32),44100
        word_limit=25
    elif model=='fish-s2-pro':
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
        server=subprocess.Popen([sys.executable,str(repo/'tools/api_server.py'),'--listen','127.0.0.1:8080',
            '--llama-checkpoint-path',checkpoint,'--decoder-checkpoint-path',checkpoint+'/codec.pth',
            '--decoder-config-name','modded_dac_vq','--half'],cwd=repo,stdout=subprocess.DEVNULL)
        import urllib.request
        for _ in range(180):
            if server.poll() is not None:raise RuntimeError('Fish model failed to load; inspect Kaggle GPU memory and runtime.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8080/v1/health',timeout=3) as response:
                    if response.status==200:break
            except OSError:time.sleep(2)
        else:raise RuntimeError('Fish model did not become ready within six minutes.')
        anchor=None;anchor_text=None
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
    from scipy.signal import resample_poly
    from math import gcd
    pieces=[];timings=[];offset=0.;sample_rate=24000
    for index,part in enumerate(request['parts']):
        start=offset
        for segment in part['segments']:
            if segment['kind']=='speech':
                for phrase in chunks(segment.get('ttsText',segment['text']),word_limit):
                    audio,sr=speak(phrase)
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
                        'timingSource':'generated-passage-duration'})
        print('Completed narration passage',index+1,'/',len(request['parts']),flush=True)
    audio=np.concatenate(pieces) if pieces else np.empty(0)
    if not len(audio):raise RuntimeError('No spoken narration was generated.')
    if float(np.max(np.abs(audio)))<.0001:raise RuntimeError('Voice model returned silent audio.')
    sf.write('/kaggle/working/narration.wav',np.clip(audio,-.99,.99),sample_rate,subtype='PCM_16')
    result={'signature':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),
        'model':model,'duration':len(audio)/sample_rate,'sampleRate':sample_rate,'sentences':timings,
        'provider':'kaggle','voiceConsistency':'generated-anchor' if model in ('dia-1.6b','fish-s2-pro') else 'built-in preset',
        'soundEffects':'pauses','setupAndGenerationSeconds':time.monotonic()-started}
    Path('/kaggle/working/result.json').write_text(json.dumps(result),encoding='utf-8')

if __name__=='__main__':
    started=time.monotonic()
    run(REQUEST)

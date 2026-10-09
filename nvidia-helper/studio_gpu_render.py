"""Optional local GPU renderer; story logic, timelines and archives stay shared.

The CPU renderer remains available on machines without the optional NVIDIA
codec library. No GPU/Torch import happens for the default CPU configuration.
"""
import importlib.util,json,os,subprocess,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from studio_render import VideoRenderer,effective_motion

ENGINE={'name':'cuda-bicubic-nvenc','version':2,'preset':'P4','qp':24,'aacCoder':'fast'}

def create_renderer(store,config):
    if config.get('renderBackend','cpu')!='native':return VideoRenderer(store,config)
    try:return NativeVideoRenderer(store,config)
    except (ImportError,OSError,RuntimeError) as error:
        result=VideoRenderer(store,config)
        result.backend_note='GPU renderer unavailable; CPU renderer selected: '+str(error)[:240]
        return result

class PhotoPreparation:
    """At most one upcoming fitted photo, one cancellable FFmpeg process."""
    def __init__(self,renderer,project,jobs):
        self.renderer=renderer;self.project_id=project['id'];self.video=project['settings']['video'];self.stop=threading.Event()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='studio-photo-fit')
        self.jobs={};self.paths=[];self.next=None
        for _,job in jobs:
            if renderer.cached_asset(project['id'],Path(job['clip'])):continue
            args=job['args'];path=args[args.index('-i')+1]
            if path not in self.jobs:self.paths.append(path);self.jobs[path]=job
        self.positions={path:index for index,path in enumerate(self.paths)}
    def prepare(self,path,gate):
        from render_queue import ClipPoolStopped
        job=self.jobs[path]
        if self.renderer.asset_identity(self.project_id,job['sourceAsset'])!=job['sourceDigest']:
            raise ValueError('The next source photo changed. Retry the updated plan; previous videos are retained.')
        v=self.video;fit=self.renderer.motion({'motion':'static','manual':{'motion':True}},dict(v,motionMode='static'),1)
        proc=subprocess.Popen([self.renderer.executable(),'-hide_banner','-v','error','-nostdin','-i',path,
            '-vf',fit,'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+30
        try:
            while True:
                if self.stop.is_set():raise ClipPoolStopped('Photo preparation stopped; saved clips are retained.')
                gate()
                if time.monotonic()>deadline:raise RuntimeError('Fitting the next photo timed out; saved clips are retained.')
                try:raw,errors=proc.communicate(timeout=.1);break
                except subprocess.TimeoutExpired:continue
            if proc.returncode:raise RuntimeError('Photo preparation failed: '+errors.decode(errors='replace')[-800:])
            if len(raw)!=v['width']*v['height']*3:raise RuntimeError('Photo preparation returned an incomplete frame.')
            if self.renderer.asset_identity(self.project_id,job['sourceAsset'])!=job['sourceDigest']:
                raise ValueError('The next source photo changed while being prepared. Its stale frame was discarded.')
            return raw
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.communicate()
    def take(self,path,gate):
        if self.next and self.next[0]==path:raw=self.next[1].result();self.next=None
        else:
            # An unexpected cache change cannot leave an unbounded chain of fits.
            if self.next:self.next[1].cancel();self.next=None
            raw=self.prepare(path,gate)
        index=self.positions[path]
        if index+1<len(self.paths):
            upcoming=self.paths[index+1];self.next=(upcoming,self.pool.submit(self.prepare,upcoming,gate))
        return raw
    def close(self):
        self.stop.set()
        if self.next:self.next[1].cancel()
        self.pool.shutdown(wait=True,cancel_futures=True);self.next=None

class NativeVideoRenderer(VideoRenderer):
    allow_legacy_render_reuse=False
    def __init__(self,store,config):
        super().__init__(store,config)
        import sys
        library=Path(__file__).resolve().parent/'render-libs'
        if library.is_dir() and str(library) not in sys.path:sys.path.insert(0,str(library))
        if importlib.util.find_spec('PyNvVideoCodec') is None:raise ImportError('Install the small optional NVIDIA codec library with install-gpu-renderer.ps1.')
        self._native=None;self.backend_note='Local NVIDIA GPU photo motion; original audio and assets retained.'
    def shot_identity(self,project,shot):
        return super().shot_identity(project,shot)|{'renderEngine':ENGINE}
    def render_capacity(self,*_):return 1
    def render_clips(self,project,jobs,render,gate,video):
        # Only missing clips trigger CUDA compilation, fitting or encoder setup.
        if not any(not self.cached_asset(project['id'],Path(job['clip'])) for _,job in jobs):
            return super().render_clips(project,jobs,render,gate,video)
        from studio_gpu_codec import codec_module
        from studio_gpu_motion import NativeMotion
        import torch
        if not torch.cuda.is_available():raise RuntimeError('NVIDIA GPU rendering is unavailable. Select CPU compatibility under Video output; completed assets are retained.')
        if torch.cuda.mem_get_info()[0]<256*2**20:raise RuntimeError('Not enough free GPU memory. Unload the image model or select CPU compatibility; completed assets are retained.')
        codec_module()
        kernel=NativeMotion();preparation=None
        try:
            preparation=PhotoPreparation(self,project,jobs)
            self._native=(kernel,preparation,video,{job['args'][-1]:job for _,job in jobs})
            return super().render_clips(project,jobs,render,gate,video)
        finally:
            self._native=None
            if preparation:preparation.close()
            kernel.close()
    def run(self,args,gate,log,multiple_outputs=False):
        context=self._native
        if context and '-frames:v' in args and args[-1] in context[3]:
            import numpy as np
            import torch
            from studio_gpu_codec import render_frames
            kernel,preparation,video,_=context;path=args[args.index('-i')+1]
            destination=Path(args[-1]);staging=destination.with_name(destination.stem+'.writing'+destination.suffix)
            source=None
            try:
                raw=preparation.take(path,gate)
                torch.cuda.set_device(kernel.anchor.device)
                source=torch.from_numpy(np.frombuffer(raw,dtype=np.uint8).copy().reshape(video['height'],video['width'],3)).to(kernel.anchor.device)
                # The immutable CPU filter contains the validated effective motion;
                # retain explicit/manual holds and the project's gentle fallback.
                job=context[3][args[-1]];shot=job['nativeShot']
                result=render_frames(source,staging,effective_motion(shot,video),int(args[args.index('-frames:v')+1]),video['fps'],
                    amount=shot.get('motionSettings',{}).get('zoomAmount',video.get('zoomAmount',.06)),
                    easing=video.get('motionEasing','linear'),kernel=kernel,qp=ENGINE['qp'],preset=ENGINE['preset'],gate=gate)
                Path(log).write_text(json.dumps(result,indent=2),encoding='utf-8')
                staging.replace(destination);self.completed_output(destination)
                return
            finally:
                if staging.is_file():staging.unlink()
                del source
        if '-c:a' in args and args[args.index('-c:a')+1]=='aac' and '-aac_coder' not in args:
            args=[*args[:-1],'-aac_coder','fast',args[-1]]
        return super().run(args,gate,log,multiple_outputs)

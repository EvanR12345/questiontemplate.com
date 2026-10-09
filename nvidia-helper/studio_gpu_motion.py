"""Fused subpixel still-motion kernel using the helper's existing CUDA runtime."""
import ctypes as C
import math,os,threading
from pathlib import Path
import torch
from studio_gpu_runtime import load_nvrtc

_PTX_CACHE={}
_PTX_LOCK=threading.Lock()

CUDA_SOURCE = r'''
__device__ float cubic_weight(float x) {
    x = x < 0 ? -x : x;
    const float a = -0.75f;
    if (x <= 1.0f) return ((a+2.0f)*x-(a+3.0f))*x*x+1.0f;
    if (x < 2.0f) return ((a*x-5.0f*a)*x+8.0f*a)*x-4.0f*a;
    return 0.0f;
}
__device__ int clamp_index(int v, int n) { return v < 0 ? 0 : (v >= n ? n-1 : v); }
__device__ float clamp_float(float v, float hi) { return v < 0 ? 0 : (v > hi ? hi : v); }
__device__ unsigned char quantize(float v) {
    v=clamp_float(v,255.0f);
    return (unsigned char)__float2int_rn(v);
}
__device__ void sample(const unsigned char* src, int w, int h, float x, float y, float* rgb) {
    int ix=(int)x, iy=(int)y;
    if(x<(float)ix) ix--; if(y<(float)iy) iy--;
    float wx[4], wy[4];
    for(int j=0;j<4;j++) {wx[j]=cubic_weight(x-(ix+j-1));wy[j]=cubic_weight(y-(iy+j-1));}
    rgb[0]=rgb[1]=rgb[2]=0;
    for(int row=0;row<4;row++) {
        int sy=clamp_index(iy+row-1,h);
        float sums[3]={0,0,0};
        for(int col=0;col<4;col++) {
            int sx=clamp_index(ix+col-1,w), pos=(sy*w+sx)*3;
            for(int ch=0;ch<3;ch++) sums[ch]+=wx[col]*(float)src[pos+ch];
        }
        for(int ch=0;ch<3;ch++) rgb[ch]+=wy[row]*sums[ch];
    }
    for(int ch=0;ch<3;ch++) rgb[ch]=(float)quantize(rgb[ch]);
}
extern "C" __global__ void warp_rgba(const unsigned char* src, unsigned char* dst,
    int w,int h,float zoom,float ox,float oy) {
    int x=blockIdx.x*blockDim.x+threadIdx.x, y=blockIdx.y*blockDim.y+threadIdx.y;
    if(x>=w || y>=h) return;
    float rgb[3]; sample(src,w,h,ox+(float)x/zoom,oy+(float)y/zoom,rgb);
    int pos=(y*w+x)*4;
    dst[pos]=quantize(rgb[0]); dst[pos+1]=quantize(rgb[1]); dst[pos+2]=quantize(rgb[2]); dst[pos+3]=255;
}
extern "C" __global__ void warp_nv12(const unsigned char* src, unsigned char* dst,
    int w,int h,float zoom,float ox,float oy) {
    int bx=(blockIdx.x*blockDim.x+threadIdx.x)*2, by=(blockIdx.y*blockDim.y+threadIdx.y)*2;
    if(bx>=w || by>=h) return;
    float mean[3]={0,0,0};
    for(int dy=0;dy<2;dy++) for(int dx=0;dx<2;dx++) {
        int x=bx+dx,y=by+dy; float rgb[3];
        sample(src,w,h,ox+(float)x/zoom,oy+(float)y/zoom,rgb);
        dst[y*w+x]=quantize(16.0f+0.256788f*rgb[0]+0.504129f*rgb[1]+0.097906f*rgb[2]);
        for(int ch=0;ch<3;ch++) mean[ch]+=rgb[ch]*0.25f;
    }
    int uv=w*h+(by/2)*w+bx;
    dst[uv]=quantize(128.0f-0.148223f*mean[0]-0.290993f*mean[1]+0.439216f*mean[2]);
    dst[uv+1]=quantize(128.0f+0.439216f*mean[0]-0.367788f*mean[1]-0.071427f*mean[2]);
}
'''

def check(code,label):
    if code:raise RuntimeError(f'{label} failed: CUDA code {code}')

def motion_coordinates(motion,width,height,index,frames,amount=.06,easing='linear'):
    if not math.isfinite(amount) or not 0<=amount<=.35:raise ValueError('Invalid zoom amount')
    if frames<1 or not 0<=index<frames:raise ValueError('Invalid frame index/count')
    if frames==1:return 1.,0.,0.
    progress=index/max(1,frames-1)
    if easing=='smooth':progress=3*progress**2-2*progress**3
    zoom=1.;x=y=None
    if motion=='slow zoom in':zoom=1+amount*progress
    elif motion=='slow zoom out':zoom=1+amount-amount*progress
    elif motion in ('pan left','pan right','pan up','pan down'):
        zoom=1.08
        if motion=='pan left':x=(width-width/zoom)*(1-progress)
        if motion=='pan right':x=(width-width/zoom)*progress
        if motion=='pan up':y=(height-height/zoom)*(1-progress)
        if motion=='pan down':y=(height-height/zoom)*progress
    elif motion!='static':raise ValueError('Unknown motion')
    return zoom,(width-width/zoom)/2 if x is None else x,(height-height/zoom)/2 if y is None else y

class NativeMotion:
    def __init__(self):
        torch.cuda.init()
        self.anchor=torch.empty((1,),device='cuda',dtype=torch.uint8)
        self.driver=C.WinDLL('nvcuda.dll') if os.name=='nt' else C.CDLL('libcuda.so.1')
        lib=Path(torch.__file__).parent/'lib'
        self.dll_handle=os.add_dll_directory(str(lib)) if os.name=='nt' else None
        self.rtc=load_nvrtc(torch.__file__)
        self.driver.cuCtxGetCurrent.argtypes=[C.POINTER(C.c_void_p)]
        self.driver.cuModuleLoadData.argtypes=[C.POINTER(C.c_void_p),C.c_void_p]
        self.driver.cuModuleGetFunction.argtypes=[C.POINTER(C.c_void_p),C.c_void_p,C.c_char_p]
        self.driver.cuModuleUnload.argtypes=[C.c_void_p]
        self.driver.cuLaunchKernel.argtypes=[C.c_void_p,*([C.c_uint]*7),C.c_void_p,C.POINTER(C.c_void_p),C.c_void_p]
        self.rtc.nvrtcCreateProgram.argtypes=[C.POINTER(C.c_void_p),C.c_char_p,C.c_char_p,C.c_int,C.c_void_p,C.c_void_p]
        self.rtc.nvrtcCompileProgram.argtypes=[C.c_void_p,C.c_int,C.POINTER(C.c_char_p)]
        self.rtc.nvrtcGetProgramLogSize.argtypes=[C.c_void_p,C.POINTER(C.c_size_t)]
        self.rtc.nvrtcGetProgramLog.argtypes=[C.c_void_p,C.c_char_p]
        self.rtc.nvrtcGetPTXSize.argtypes=[C.c_void_p,C.POINTER(C.c_size_t)]
        self.rtc.nvrtcGetPTX.argtypes=[C.c_void_p,C.c_char_p]
        self.rtc.nvrtcDestroyProgram.argtypes=[C.POINTER(C.c_void_p)]
        self.context=C.c_void_p();check(self.driver.cuCtxGetCurrent(C.byref(self.context)),'get context')
        major,minor=torch.cuda.get_device_capability()
        # Keep the small compiled program in helper RAM, without retaining image
        # buffers or a GPU encoder across chapters. Model/audio switching is safe.
        architecture=min(major*10+minor,90)
        cache_key=(architecture,torch.version.cuda,CUDA_SOURCE)
        with _PTX_LOCK:
            compiled=_PTX_CACHE.get(cache_key)
            if compiled is None:
                program=C.c_void_p()
                check(self.rtc.nvrtcCreateProgram(C.byref(program),CUDA_SOURCE.encode(),b'studio_motion.cu',0,None,None),'create program')
                try:
                    options=(C.c_char_p*3)(f'--gpu-architecture=compute_{architecture}'.encode(),b'--std=c++11',b'--fmad=false')
                    code=self.rtc.nvrtcCompileProgram(program,len(options),options)
                    if code:
                        size=C.c_size_t();self.rtc.nvrtcGetProgramLogSize(program,C.byref(size))
                        log=C.create_string_buffer(size.value);self.rtc.nvrtcGetProgramLog(program,log)
                        raise RuntimeError('Motion kernel compile: '+log.value.decode(errors='replace'))
                    size=C.c_size_t();check(self.rtc.nvrtcGetPTXSize(program,C.byref(size)),'get PTX size')
                    ptx=C.create_string_buffer(size.value);check(self.rtc.nvrtcGetPTX(program,ptx),'get PTX')
                    compiled=ptx.raw;_PTX_CACHE[cache_key]=compiled
                finally:self.rtc.nvrtcDestroyProgram(C.byref(program))
        ptx=C.create_string_buffer(compiled)
        self.module=C.c_void_p();check(self.driver.cuModuleLoadData(C.byref(self.module),ptx),'load PTX')
        self.functions={}
        for name in ('warp_rgba','warp_nv12'):
            fn=C.c_void_p();check(self.driver.cuModuleGetFunction(C.byref(fn),self.module,name.encode()),'get kernel')
            self.functions[name]=fn

    def launch(self,source,output,width,height,zoom,x,y,format='rgba',stream=None):
        if format not in ('rgba','nv12') or width<=0 or height<=0:raise ValueError('Invalid motion format/dimensions')
        if not source.is_cuda or not output.is_cuda:raise ValueError('CUDA buffers required')
        if source.shape!=(height,width,3):raise ValueError('Source buffer size mismatch')
        required=width*height*(3/2 if format=='nv12' else 4)
        if output.numel()!=required:raise ValueError('Output buffer size mismatch')
        if not all(math.isfinite(value) for value in (zoom,x,y)) or zoom<1:raise ValueError('Invalid motion transform')
        if width%2 or height%2 or not source.is_contiguous() or not output.is_contiguous():raise ValueError('Even dimensions and contiguous buffers required')
        if source.dtype!=torch.uint8 or output.dtype!=torch.uint8 or source.device!=output.device:raise ValueError('Invalid CUDA buffers')
        if source.device!=self.anchor.device:raise ValueError('Motion kernel and buffers must use the same CUDA device')
        stream=stream or torch.cuda.current_stream()
        values=[C.c_uint64(source.data_ptr()),C.c_uint64(output.data_ptr()),C.c_int(width),C.c_int(height),C.c_float(zoom),C.c_float(x),C.c_float(y)]
        params=(C.c_void_p*len(values))(*(C.cast(C.byref(v),C.c_void_p).value for v in values))
        factor=2 if format=='nv12' else 1
        name='warp_nv12' if format=='nv12' else 'warp_rgba'
        check(self.driver.cuLaunchKernel(self.functions[name],(width//factor+15)//16,(height//factor+15)//16,1,16,16,1,0,
            C.c_void_p(stream.cuda_stream),params,None),'launch motion')

    def close(self):
        if getattr(self,'module',None):
            torch.cuda.set_device(self.anchor.device)
            torch.cuda.synchronize();check(self.driver.cuModuleUnload(self.module),'unload motion');self.module=None
        if self.dll_handle:self.dll_handle.close();self.dll_handle=None

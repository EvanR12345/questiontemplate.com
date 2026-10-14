"""GPU-resident transform, colour conversion and hardware H.264 encoding."""
import os,sys,time
from pathlib import Path
import torch
from studio_gpu_motion import NativeMotion,motion_coordinates

_dll_handles=[]

class EncoderFrame:
    """Bridge legacy positional DLPack consumers to keyword-only Torch APIs.

    The tensor remains the memory owner. Only the protocol call is adapted;
    no pixels are copied, resampled or moved off the GPU.
    """
    def __init__(self,tensor):self.tensor=tensor
    def __dlpack__(self,stream=None):return self.tensor.__dlpack__(stream=stream)
    def __dlpack_device__(self):return self.tensor.__dlpack_device__()
    def cuda(self):
        # PyNvVideoCodec's documented GPU input path expects one CUDA array
        # interface per NV12 plane. This also avoids its Torch type special case.
        height3,width=self.tensor.shape
        if height3%3 or width%2 or not self.tensor.is_cuda or not self.tensor.is_contiguous() or self.tensor.element_size()!=1:
            raise ValueError('Encoder requires contiguous GPU NV12 bytes.')
        height=height3*2//3;pointer=self.tensor.data_ptr()
        return [EncoderPlane(pointer,(height,width,1),(width,1,1)),
                EncoderPlane(pointer+height*width,(height//2,width//2,2),(width,2,1))]

class EncoderPlane:
    def __init__(self,pointer,shape,strides):
        self.__cuda_array_interface__={'version':3,'shape':shape,'strides':strides,
            'typestr':'|u1','data':(pointer,False)}

def mp4_timescale(fps):
    """Match FFmpeg's default MP4 track clock for mixed CPU/GPU clips."""
    result=fps
    while result<10000:result*=2
    return result
def codec_module():
    folder=Path(__file__).resolve().parent/'render-libs'
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))
    if os.name=='nt' and not _dll_handles:_dll_handles.append(os.add_dll_directory(str(Path(torch.__file__).parent/'lib')))
    import PyNvVideoCodec as nvc
    return nvc

def render_frames(source,output_path,motion,frames=150,fps=30,amount=.06,easing='linear',preset='P4',qp=21,kernel=None,gate=lambda *_:None):
    nvc=codec_module();height,width=source.shape[:2]
    if width%2 or height%2 or not source.is_contiguous() or source.dtype!=torch.uint8:raise ValueError('Invalid source')
    if isinstance(frames,bool) or not isinstance(frames,int) or frames<1:raise ValueError('Invalid frame count')
    if fps not in (24,25,30,60) or not 0<=qp<=51:raise ValueError('Invalid frame rate or quantizer')
    owned_kernel=kernel is None
    kernel=kernel or NativeMotion()
    stream=torch.cuda.current_stream()
    output=torch.empty((height*3//2,width),device=source.device,dtype=torch.uint8)
    encoder_frame=EncoderFrame(output)
    began=time.perf_counter();enc=nvc.CreateEncoder(width,height,'NV12',False,codec='h264',preset=preset,
        fps=fps,rc='constqp',constqp=qp,bf=2,gop=250,profile='high',tuning_info='high_quality',
        cudacontext=kernel.context.value,cudastream=stream.cuda_stream)
    initialized=time.perf_counter()-began
    mux=nvc.FFmpegMuxer(file_path=str(output_path),media_format=nvc.GetMediaFormat(str(output_path)),codec='h264',
        width=width,height=height,fps_num=fps,fps_den=1,timebase_num=1,timebase_den=mp4_timescale(fps),extradata=enc.GetSequenceParams())
    mux.SetUniformPtsIncrement(mp4_timescale(fps)//fps)
    encode_seconds=0.;warp_seconds=0.;chunks=0
    try:
        for index in range(frames):
            gate(f'Rendering frame {index+1} / {frames}')
            started=time.perf_counter()
            zoom,x,y=motion_coordinates(motion,width,height,index,frames,amount,easing)
            kernel.launch(source,output,width,height,zoom,x,y,format='nv12',stream=stream)
            stream.synchronize()
            warp_seconds+=time.perf_counter()-started
            started=time.perf_counter();pic=nvc.NV_ENC_PIC_PARAMS();pic.inputTimeStamp=index
            packets=enc.Encode(encoder_frame,pic)
            for packet in packets:
                mux.MuxVideoPacket(bytes(packet['data']),packet['picture_type'],packet['timestamp']);chunks+=1
            encode_seconds+=time.perf_counter()-started
        for packet in enc.EndEncode():
            mux.MuxVideoPacket(bytes(packet['data']),packet['picture_type'],packet['timestamp']);chunks+=1
        mux.Finalize()
        del mux; mux=None
        del enc; enc=None
        return {'seconds':time.perf_counter()-began,'encoderInitSeconds':initialized,'warpSyncSeconds':warp_seconds,
            'encodeMuxCallSeconds':encode_seconds,'encodedPackets':chunks,'fps':fps,'frames':frames,
            'encoder':'native_nvenc','preset':preset,'qp':qp,'frameReadback':False,'colourFormat':'NV12_BT601_limited',
            'outputBytes':Path(output_path).stat().st_size,'torchPeakBytes':torch.cuda.max_memory_allocated()}
    finally:
        del enc,mux,encoder_frame,output
        if owned_kernel:kernel.close()

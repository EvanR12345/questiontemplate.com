import unittest
import torch
from studio_gpu_codec import EncoderFrame

class EncoderFrameTest(unittest.TestCase):
    def test_legacy_encoder_positional_stream_reaches_keyword_only_tensor(self):
        calls=[];capsule=object()
        class Tensor:
            def __dlpack__(self,*,stream=None):calls.append(stream);return capsule
            def __dlpack_device__(self):return (2,0)
        frame=EncoderFrame(Tensor())
        self.assertIs(frame.__dlpack__(123456),capsule)
        self.assertEqual(calls,[123456]);self.assertEqual(frame.__dlpack_device__(),(2,0))

    def test_real_tensor_roundtrip_retains_bytes_and_same_memory(self):
        original=torch.arange(24,dtype=torch.uint8).reshape(4,6)
        restored=torch.from_dlpack(EncoderFrame(original))
        self.assertEqual(restored.data_ptr(),original.data_ptr())
        self.assertTrue(torch.equal(restored,original))
        original[0,0]=99;self.assertEqual(restored[0,0].item(),99)

    def test_nv12_planes_keep_exact_gpu_pointer_offsets_and_byte_strides(self):
        class Tensor:
            shape=(1080,1280);is_cuda=True
            def is_contiguous(self):return True
            def element_size(self):return 1
            def data_ptr(self):return 4096
        planes=EncoderFrame(Tensor()).cuda()
        self.assertEqual(planes[0].__cuda_array_interface__['shape'],(720,1280,1))
        uv=planes[1].__cuda_array_interface__
        self.assertEqual(uv['shape'],(360,640,2));self.assertEqual(uv['strides'],(1280,2,1))
        self.assertEqual(uv['data'],(4096+720*1280,False))

if __name__=='__main__':unittest.main()

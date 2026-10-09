import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore,new_project
from studio_render import VideoRenderer
from studio_gpu_render import NativeVideoRenderer,create_renderer,ENGINE

class GPUSelectionTest(unittest.TestCase):
    def test_cpu_configuration_never_imports_gpu_runtime(self):
        with patch('studio_gpu_render.NativeVideoRenderer',side_effect=AssertionError('GPU import')):
            self.assertIs(type(create_renderer(None,{})),VideoRenderer)
            self.assertIs(type(create_renderer(None,{'renderBackend':'cpu'})),VideoRenderer)
    def test_unavailable_native_backend_is_explicit_cpu_fallback(self):
        with patch('studio_gpu_render.NativeVideoRenderer',side_effect=ImportError('optional codec missing')):
            result=create_renderer(None,{'renderBackend':'native'})
            self.assertIs(type(result),VideoRenderer)
            self.assertIn('CPU renderer selected',result.backend_note)
            self.assertTrue(result.allow_legacy_render_reuse)
    def test_gpu_cache_does_not_adopt_cpu_clips_as_gpu_results(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder);p=store.save(new_project())
            store.asset(p['id'],'image.png').write_bytes(b'image')
            shot={'imagePath':'image.png','start':0,'end':2,'motion':'pan right'}
            cpu=VideoRenderer(store,{})
            gpu=NativeVideoRenderer.__new__(NativeVideoRenderer);VideoRenderer.__init__(gpu,store,{})
            identity=gpu.shot_identity(p,shot)
            self.assertNotEqual(identity,cpu.shot_identity(p,shot))
            self.assertEqual(identity['renderEngine'],ENGINE)
            self.assertFalse(gpu.allow_legacy_render_reuse)
            self.assertEqual(identity,gpu.shot_identity(p,shot|{'start':2,'end':4}))
            store.asset(p['id'],'image.png').write_bytes(b'changed image')
            self.assertNotEqual(identity,gpu.shot_identity(p,shot))
    def test_native_audio_uses_requested_128k_and_atomic_shared_runner(self):
        gpu=NativeVideoRenderer.__new__(NativeVideoRenderer);gpu._native=None
        args=['-i','master.wav','-c:a','aac','-b:a','128k','destination.m4a']
        with patch.object(VideoRenderer,'run') as run:
            gpu.run(args,lambda *_:None,'log')
        forwarded=run.call_args.args[0]
        self.assertEqual(forwarded[forwarded.index('-b:a')+1],'128k')
        self.assertEqual(forwarded[forwarded.index('-aac_coder')+1],'fast')
        self.assertEqual(forwarded[-1],'destination.m4a')
        self.assertNotIn('-aac_coder',args)

if __name__=='__main__':unittest.main()

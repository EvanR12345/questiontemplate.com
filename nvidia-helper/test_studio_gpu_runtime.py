import tempfile
import unittest
from pathlib import Path
from studio_gpu_runtime import nvrtc_candidates, load_nvrtc


class RuntimeTests(unittest.TestCase):
    def test_bundled_linux_wheel_before_system_name(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'torch').mkdir()
            runtime = base / 'nvidia' / 'cuda_nvrtc' / 'lib'
            runtime.mkdir(parents=True)
            (runtime / 'libnvrtc.so.12').touch()
            candidates = nvrtc_candidates(base / 'torch' / '__init__.py', 'posix', lambda _:None)
            self.assertEqual(candidates[0],str(runtime / 'libnvrtc.so.12'))
            self.assertEqual(len(candidates),len(set(candidates)))

    def test_windows_version_not_hardcoded(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = Path(folder) / 'torch' / 'lib'
            lib.mkdir(parents=True)
            (lib / 'nvrtc64_130_0.dll').touch()
            self.assertEqual(nvrtc_candidates(lib.parent / '__init__.py','nt'),
                             [str(lib / 'nvrtc64_130_0.dll')])

    def test_unloadable_candidate_falls_through(self):
        seen=[]
        def loader(name):
            seen.append(name)
            if name=='libnvrtc.so.12':return 'loaded'
            raise OSError('not found')
        self.assertEqual(load_nvrtc('/no/torch/__init__.py','posix',loader,lambda _:None),'loaded')
        self.assertGreater(len(seen),1)

    def test_clear_error_and_no_download(self):
        def loader(_):raise OSError('not found')
        with self.assertRaisesRegex(RuntimeError,'CPU compatibility.*No additional runtime'):
            load_nvrtc('/no/torch/__init__.py','posix',loader,lambda _:None)


if __name__=='__main__':unittest.main()

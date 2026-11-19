"""Cached speech can stay unloaded while Studio serves saved media."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest

from server import CudaEngine


class CachedVoiceTest(unittest.TestCase):
    def test_requires_local_weights_default_voice_and_valid_vocab(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            (base/'voices').mkdir()
            (base/'config.json').write_text(json.dumps({'vocab':{'h':3}}))
            (base/'kokoro-v1_0.pth').write_bytes(b'weights')
            hub=SimpleNamespace(try_to_load_from_cache=lambda _repo,name:str(base/name))
            with patch.dict(sys.modules,{'huggingface_hub':hub}):
                self.assertIsNone(CudaEngine.cached_vocab())
                (base/'voices/am_michael.pt').write_bytes(b'weights')
                self.assertEqual(CudaEngine.cached_vocab(),{'h':3})
                (base/'config.json').write_text('{"vocab":{"h":true}}')
                self.assertIsNone(CudaEngine.cached_vocab())

    def test_concurrent_first_requests_load_once_and_warm_default_first(self):
        engine=CudaEngine.__new__(CudaEngine)
        engine.model=None;engine._load_lock=threading.RLock();engine.device='cuda:0'
        engine.voices={};engine.warm=set();engine._vocab={'h':3}
        model=Mock();model.to.return_value=model;model.eval.return_value=model;model.vocab={'h':3}
        factory=Mock(return_value=model)
        prepared=[]
        def warm(voice):
            if voice not in engine.warm:
                prepared.append(voice);engine.warm.add(voice)
        engine._prepare_voice=warm
        with patch.dict(sys.modules,{'kokoro':SimpleNamespace(KModel=factory)}):
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(engine.prepare,['af_heart']*3))
        factory.assert_called_once()
        self.assertEqual(prepared,['am_michael','af_heart'])

    def test_failed_first_warmup_can_retry(self):
        engine=CudaEngine.__new__(CudaEngine)
        engine.model=None;engine._load_lock=threading.RLock();engine.device='cuda:0';engine.voices={};engine.warm=set()
        model=Mock();model.to.return_value=model;model.eval.return_value=model;model.vocab={'h':3}
        factory=Mock(return_value=model)
        engine._prepare_voice=Mock(side_effect=[RuntimeError('unavailable'),None,None])
        with patch.dict(sys.modules,{'kokoro':SimpleNamespace(KModel=factory)}):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):engine.prepare('af_heart')
            self.assertIsNone(engine.model)
            engine.prepare('af_heart')
        self.assertEqual(factory.call_count,2)

if __name__=='__main__':unittest.main()

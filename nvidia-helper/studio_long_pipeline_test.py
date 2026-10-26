import hashlib,io,json,tempfile,time,unittest,wave
from pathlib import Path
from unittest.mock import patch
from studio_cloud_inputs import CloudInputCache
from studio_cloud_render_plan import render_plan,timeout_for_bytes
from studio_data import new_project
from studio_voice_provider import KaggleVoice


class LongPipelineTest(unittest.TestCase):
    def test_twelve_hour_plan_counts_working_copies_and_keeps_laptop_output_zero(self):
        p=new_project();p['chapters'][0].update(sourceText='A saved story.',audio={'duration':43200,'path':'voice.wav'})
        plan=render_plan(p,1000000)
        self.assertEqual(plan['durationSeconds'],43200)
        self.assertGreater(plan['suggestedExecutionSeconds'],900)
        self.assertGreater(plan['recommendedDiskGB'],25)
        self.assertEqual(plan['laptopVideoBytes'],0)
        self.assertFalse(plan['longExportMeasured'])
        p['settings']['engagement']['splitEnabled']=True
        self.assertGreater(render_plan(p)['scratchEstimateBytes'],plan['scratchEstimateBytes'])
        self.assertGreater(render_plan(p,20*2**30)['scratchEstimateBytes']-render_plan(p,2*2**30)['scratchEstimateBytes'],18*2**30)

    def test_large_transfer_gets_a_duration_bound_not_a_fixed_two_minute_cutoff(self):
        self.assertGreater(timeout_for_bytes(15*2**30,8000),120)
        self.assertEqual(timeout_for_bytes(15*2**30,31),31)
        with self.assertRaises(ValueError):timeout_for_bytes(1,0)

    def test_lazy_inputs_fetch_only_requested_bytes_and_refresh_the_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[];payload=b'first image';sha=hashlib.sha256(payload).hexdigest()
            records=[{'path':'one.png','bytes':len(payload),'sha256':sha},{'path':'unused.png','bytes':len(payload),'sha256':sha}]
            def capability(path):calls.append(path);return dict(records[0],url='https://storage.googleapis.com/fresh')
            cache=CloudInputCache(records,lambda p:Path(directory)/p,capability,lambda *_:None,time.time()+500,lambda u:u,
                opener=lambda *a,**kw:io.BytesIO(payload))
            self.assertFalse((Path(directory)/'one.png').exists())
            self.assertEqual(cache.ensure('one.png').read_bytes(),payload)
            cache.ensure('one.png');self.assertEqual(calls,['one.png'])
            self.assertFalse((Path(directory)/'unused.png').exists())
            self.assertEqual(cache.stats()['files'],1)
            (Path(directory)/'one.png').write_bytes(b'wrong bytes')
            with self.assertRaisesRegex(ValueError,'saved identity'):cache.ensure('one.png')

    def test_corrupt_or_rebound_cloud_input_is_never_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            r={'path':'one.png','bytes':4,'sha256':hashlib.sha256(b'good').hexdigest()}
            for reply in [dict(r,url='https://storage.googleapis.com/new'),dict(r,bytes=99,url='https://storage.googleapis.com/new')]:
                cache=CloudInputCache([r],lambda p:Path(directory)/p,lambda _:reply,lambda *_:None,time.time()+500,lambda u:u,
                    opener=lambda *a,**kw:io.BytesIO(b'evil'))
                with self.assertRaises(ValueError):cache.ensure('one.png')
                self.assertFalse((Path(directory)/'one.png').exists())
                self.assertFalse(list(Path(directory).glob('*partial')))

    def test_one_voice_model_load_writes_separate_chapters_without_spoken_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'ch1').mkdir();(root/'ch2').mkdir()
            items=[{'id':'one','text':'The gate opened.','signature':'one','target':str(root/'ch1'/'a.wav')},
                   {'id':'two','text':'He stepped inside.','signature':'two','target':str(root/'ch2'/'b.wav')}]
            provider=KaggleVoice({});calls=[]
            def generate(text,target,settings,gate,**kw):
                calls.append(kw)
                self.assertEqual([p['chapterId'] for p in kw['parts_override']],['one','two'])
                with wave.open(str(target),'wb') as f:
                    f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
                    f.writeframes(b'\x01\x00'*24000+b'\x02\x00'*48000)
                return {'model':'qwen3-tts-1.7b','duration':3,'sentences':[
                    {'index':0,'text':items[0]['text'],'start':0,'end':1},
                    {'index':1,'text':items[1]['text'],'start':1,'end':3}]}
            with patch.object(provider,'_generate',side_effect=generate):
                results=provider.generate_many(items,{'voiceEngine':'qwen3-tts-1.7b'},lambda *_:None)
                cached=provider.generate_many(items,{'voiceEngine':'qwen3-tts-1.7b'},lambda *_:None)
            self.assertEqual(len(calls),1)
            self.assertEqual([r['duration'] for r in results],[1,2])
            self.assertEqual(results[1]['sentences'][0]['start'],0)
            self.assertEqual(results[1]['sentences'][0]['index'],0)
            self.assertEqual(cached,results)
            with wave.open(items[0]['target'],'rb') as f:self.assertEqual(f.readframes(1),b'\x01\x00')
            with wave.open(items[1]['target'],'rb') as f:self.assertEqual(f.readframes(1),b'\x02\x00')

    def test_manual_chapter_regeneration_does_not_prefetch_other_chapters(self):
        from studio_voice_batches import grouped_narration
        from types import SimpleNamespace
        import threading
        service=SimpleNamespace(cv=threading.Condition(),current=None,voice_quota_fallback_active=lambda *_:False)
        p=new_project();p['settings']['voiceEngine']='qwen3-tts-1.7b'
        with patch.object(KaggleVoice,'generate_many',side_effect=AssertionError('Unexpected notebook')):
            self.assertIsNone(grouped_narration(service,p,p['chapters'][0]))

    def test_partial_voice_group_generates_only_missing_chapters_and_archives_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'ch1').mkdir();(root/'ch2').mkdir()
            first=root/'ch1'/'a.wav';first.write_bytes(b'completed audio')
            result={'path':str(first),'duration':1,'sentences':[]}
            first.with_suffix('.voice-ready.json').write_text(json.dumps({'inputSignature':'one',
                'sha256':hashlib.sha256(first.read_bytes()).hexdigest(),'result':result}))
            items=[{'id':'one','text':'The gate opened.','signature':'one','target':str(first)},
                {'id':'two','text':'He stepped inside.','signature':'two','target':str(root/'ch2'/'b.wav')}]
            provider=KaggleVoice({})
            def generate(text,target,*args,**kwargs):
                self.assertEqual(text,items[1]['text'])
                self.assertEqual(len(kwargs['parts_override']),1)
                anchor=target.parent/('voice-anchor-'+'a'*20+'.wav');anchor.write_bytes(b'anchor')
                with wave.open(str(target),'wb') as output:
                    output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000);output.writeframes(b'\0\0'*24000)
                return {'duration':1,'sentences':[{'text':text,'start':0,'end':1}],
                    'voiceAnchor':{'text':text,'path':str(anchor)}}
            with patch.object(provider,'_generate',side_effect=generate):
                results=provider.generate_many(items,{'voiceEngine':'dia-1.6b'},lambda *_:None)
            self.assertEqual(results[0],result)
            self.assertEqual(Path(results[1]['voiceAnchor']['path']).parent,root)
            self.assertTrue(Path(results[1]['voiceAnchor']['path']).is_file())

    def test_cloud_only_voice_receipts_restore_before_reuse_without_another_notebook(self):
        import sqlite3,threading
        from types import SimpleNamespace
        from studio_data import ProjectStore,new_chapter
        from studio_voice_batches import grouped_narration
        with tempfile.TemporaryDirectory() as directory:
            store=ProjectStore(directory);p=store.save(new_project());p['chapters'].append(new_chapter(2))
            p['settings']['voiceEngine']='qwen3-tts-1.7b';remote={}
            for ch in p['chapters']:
                ch['sourceText']='The gate opened.'
                name=f"{ch['id']}/chapter-{ch['number']:03d}-{'a'*12}.wav"
                remote[name]=b'completed narration'
                remote[str(Path(name).with_suffix('.voice-ready.json')).replace('\\','/')]=json.dumps({
                    'inputSignature':'a'*64,'sha256':hashlib.sha256(remote[name]).hexdigest(),
                    'result':{'path':'C:/another-machine/old.wav','duration':1,'sentences':[]}}).encode()
            def fetch(pid,name):
                target=store.asset_local(pid,name)
                if name in remote:target.parent.mkdir(exist_ok=True);target.write_bytes(remote[name])
                return target
            store.archive=SimpleNamespace(fetch_asset=fetch)
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            db.executescript("CREATE TABLE jobs(id TEXT,kind TEXT);INSERT INTO jobs VALUES('job','produce-story');")
            service=SimpleNamespace(store=store,config={},cv=threading.Condition(),current='job',db=db,voice_quota_fallback_active=lambda *_:False,
                saved_narration_ready=lambda *_:False,narration_signature=lambda *_:'a'*64,
                narration_text=lambda *_:'The gate opened.',gate=lambda *_:None)
            with patch.object(KaggleVoice,'_generate',side_effect=AssertionError('Duplicate notebook')):
                result=grouped_narration(service,p,p['chapters'][0])
            self.assertEqual(Path(result['path']).read_bytes(),b'completed narration')
            self.assertTrue(Path(result['path']).is_relative_to(store.folder(p['id'])))
            db.close()

    def test_exact_chapter_cache_survives_an_unselected_previous_export(self):
        from studio_render import VideoRenderer
        from studio_data import ProjectStore
        with tempfile.TemporaryDirectory() as directory:
            store=ProjectStore(directory);p=store.save(new_project());ch=p['chapters'][0]
            ch.update(sourceText='The door opened.',cleanNarrationText='The door opened.',
                audio={'path':'voice.wav','duration':1},scenes=[{'shots':[{'id':'shot1',
                'start':0,'end':1,'imagePath':'one.png','motion':'static','manual':{'motion':True}}]}])
            store.asset(p['id'],'one.png').write_bytes(b'fixture-image')
            store.asset(p['id'],'voice.wav').write_bytes(b'fixture-audio')
            renderer=VideoRenderer(store,{})
            def encode(args,*rest,**kw):Path(args[-1]).write_bytes(b'fixture-encoded-output')
            with patch.object(renderer,'run',side_effect=encode),patch('studio_render.qc_decision',return_value={'blocking':False}):
                first=renderer.chapter(p,ch,lambda *_:None)
            ch['render']={}
            with patch.object(renderer,'run',side_effect=AssertionError('Unchanged chapter encoded again')),patch('studio_render.qc_decision',return_value={'blocking':False}):
                recovered=renderer.chapter(p,ch,lambda *_:None)
            self.assertTrue(recovered['reused']);self.assertEqual(first['path'],recovered['path'])

if __name__=='__main__':unittest.main()

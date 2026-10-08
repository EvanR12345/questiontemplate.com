import copy, json, os, subprocess, tempfile, time, unittest, wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from studio_data import new_project, new_chapter, ProjectStore
from studio_engagement import settings, validate, schedule
from studio_render import VideoRenderer
from studio_cloud_render import CloudRenderJob, main
from studio_cloud_test import Objects
from studio_storage import R2Archive, sha
from PIL import Image


class ExportSettingsTests(unittest.TestCase):
    def test_patreon_does_not_alter_youtube_or_chapter_narration(self):
        p=new_project('Destination test');p['settings']['engagement'].update(
            outroEnabled=True,popupEnabled=True,outroText='Visit my Patreon.',exportDestination='patreon')
        before=copy.deepcopy(p)
        s=settings(p)
        self.assertFalse(s['outroEnabled']);self.assertFalse(s['popupEnabled'])
        self.assertEqual(schedule(p,7200),[]);self.assertEqual(p,before)
        p['settings']['engagement']['exportDestination']='youtube'
        self.assertTrue(settings(p)['outroEnabled']);self.assertTrue(settings(p)['popupEnabled'])
        self.assertTrue(schedule(p,7200))

    def test_invalid_group_sizes_and_destinations_are_rejected(self):
        for value in (0,-1,True,1.5,'2'):
            with self.assertRaises(ValueError):validate({'chaptersPerPart':value})
        with self.assertRaises(ValueError):validate({'exportDestination':'unknown'})
        with self.assertRaises(ValueError):validate({'splitMode':'paragraphs'})
        self.assertEqual(validate({})['splitMode'],'duration')

    def test_grouped_parts_preserve_chapters_and_intro_placement_without_recursion(self):
        p=new_project('Parts');p['intro'].update(enabled=True,placement='full_story_only')
        p['chapters']=[new_chapter(i) for i in range(1,6)]
        for ch in p['chapters']:ch['sourceText']='Story content'
        p['settings']['engagement'].update(splitEnabled=True,splitMode='chapters',exportDestination='patreon')
        before=copy.deepcopy(p);renderer=VideoRenderer(None,{})
        seen=[]
        def full(part,gate):
            seen.append(copy.deepcopy(part))
            return {'path':f'part{len(seen)}.mp4','duration':10*len(part['chapters']),
                'exportDestination':settings(part)['exportDestination']}
        renderer.full=full
        result=renderer.split_chapters(p,lambda message:None,2)
        self.assertEqual([x['chapterIds'] for x in result],[[c['id'] for c in p['chapters'][0:2]],
            [c['id'] for c in p['chapters'][2:4]],[p['chapters'][4]['id']]])
        self.assertEqual([x['intro']['enabled'] for x in seen],[True,False,False])
        self.assertTrue(all(not x['settings']['engagement']['splitEnabled'] for x in seen))
        self.assertTrue(all(not settings(x)['outroEnabled'] for x in seen))
        self.assertEqual([x['start'] for x in result],[0,20,40]);self.assertEqual(p,before)


class CloudRenderTests(unittest.TestCase):
    def fixture(self, folder):
        p=new_project('Cloud render');obj=Objects();job_id='a'*32
        manifest={'version':1,'project':p,'files':{},'savedAt':0}
        obj.put_json(f'studio/manifests/{p["id"]}.json',manifest,create=True)
        obj.put_json(f'studio/render-jobs/{job_id}.json',{'id':job_id,'project':p['id'],
            'revision':p['revision'],'destination':'patreon','status':'QUEUED'},create=True)
        class Renderer:
            def __init__(self,store,config):self.store=store
            def full(self,project,gate):
                gate('Rendering test fixture')
                target=self.store.asset(project['id'],'fixture.mp4');target.write_bytes(b'test fixture, not a playable video')
                self.on_output(target)
                return {'path':'fixture.mp4','duration':2,'exportDestination':settings(project)['exportDestination']}
        return p,obj,job_id,CloudRenderJob(obj,job_id,folder,Renderer)

    def test_cloud_job_claims_saves_outputs_and_publishes_destination_without_local_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            p,obj,job_id,job=self.fixture(folder);result=job.run()
            saved=json.loads(obj.read(f'studio/manifests/{p["id"]}.json')[0])
            self.assertEqual(result['status'],'COMPLETE')
            self.assertIn('fixture.mp4',saved['files'])
            self.assertEqual(obj.uploads,1,'Final publication must not re-upload an already verified checkpoint.')
            self.assertEqual(saved['project']['exports']['patreon']['path'],'fixture.mp4')
            self.assertEqual(job.run()['status'],'COMPLETE')
            self.assertEqual(saved['project']['chapters'],p['chapters'])

    def test_concurrent_story_edit_cannot_be_overwritten_by_render(self):
        with tempfile.TemporaryDirectory() as folder:
            p,obj,job_id,job=self.fixture(folder);original=job.factory
            class EditingRenderer(original):
                def full(self,project,gate):
                    result=super().full(project,gate)
                    key=f'studio/manifests/{p["id"]}.json';raw,etag=obj.read(key);edited=json.loads(raw)
                    edited['project']['name']='New website edit';edited['project']['revision']+=1
                    obj.put_json(key,edited,etag=etag)
                    return result
            job.factory=EditingRenderer
            with self.assertRaises(ValueError):job.run()
            current=json.loads(obj.read(f'studio/manifests/{p["id"]}.json')[0])
            self.assertEqual(current['project']['name'],'New website edit')
            status=json.loads(obj.read(f'studio/render-jobs/{job_id}.json')[0])
            self.assertEqual(status['status'],'FAILED');self.assertIn('fixture.mp4',status['files'])

    def test_duplicate_running_job_and_laptop_entry_point_refuse_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            p,obj,job_id,job=self.fixture(folder);state,etag=job.state()
            state['status']='RENDERING';obj.put_json(job.key,state,etag=etag)
            with self.assertRaisesRegex(ValueError,'already running'):job.run()
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'refuses laptop'):main()

    def test_parallel_clip_checkpoints_cannot_race_saved_job_state(self):
        with tempfile.TemporaryDirectory() as folder:
            p,obj,job_id,job=self.fixture(folder)
            original=obj.put_json
            def slow_write(key,*args,**kwargs):
                if key==job.key:time.sleep(.01)
                return original(key,*args,**kwargs)
            obj.put_json=slow_write
            renderer=job.factory
            class ParallelRenderer(renderer):
                def full(self,project,gate):
                    def clip(i):
                        path=self.store.asset(project['id'],f'clip-{i}.mp4')
                        path.write_bytes(b'synthetic clip checkpoint');self.on_output(path)
                    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(clip,range(6)))
                    return super().full(project,gate)
            job.factory=ParallelRenderer
            result=job.run();self.assertEqual(result['status'],'COMPLETE')
            self.assertEqual(len(result['files']),7)

    @unittest.skipUnless(os.environ.get('STUDIO_TEST_FFMPEG'),'Set STUDIO_TEST_FFMPEG for real FFmpeg verification')
    def test_real_ffmpeg_two_chapters_restore_render_split_and_decode(self):
        # Real encoding and decode, with an isolated in-memory archive.
        # This verifies media logic; it is not a live Google compute test.
        with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as scratch:
            store=ProjectStore(original);p=new_project('Chapter boundaries')
            p['settings']['video'].update(width=640,height=360,fps=24,motionMode='static',transition='cut')
            p['settings']['engagement'].update(exportDestination='patreon',splitEnabled=True,
                splitMode='chapters',chaptersPerPart=1,outroEnabled=True,outroText='Visit Patreon')
            p['chapters']=[new_chapter(1),new_chapter(2)];p=store.save(p)
            folder=store.folder(p['id']);original_hashes={}
            for i,c in enumerate(p['chapters']):
                audio=f'chapter-{i+1:03d}.wav';image=f'art-{i}.png';scene=f'scene-{i}'
                with wave.open(str(folder/audio),'wb') as wav:
                    wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(b'\0'*48000)
                Image.new('RGB',(640,360),['purple','green'][i]).save(folder/image)
                original_hashes[audio]=sha(folder/audio)
                c.update(sourceText='Story narration.',cleanNarrationText='Story narration.',
                    audio={'path':audio,'duration':1},scenes=[{'id':scene,'shots':[{
                        'id':f'shot-{i}','sceneId':scene,'chapterId':c['id'],'start':0,'end':1,
                        'characters':[],'generationSettings':{},'camera':{},'status':'COMPLETE',
                        'imagePath':image,'motion':'static','qc':{'pass':True,'status':'PASSED'}}]}])
            p=store.save(p);obj=Objects()
            archive=R2Archive(store,Path(original)/'missing-config');archive.close()
            archive.objects=obj;archive.enabled=True;archive.sync(p['id'])
            job_id='c'*32;obj.put_json(f'studio/render-jobs/{job_id}.json',{
                'id':job_id,'project':p['id'],'revision':p['revision'],'destination':'patreon','status':'QUEUED'},create=True)
            ffmpeg=os.environ['STUDIO_TEST_FFMPEG']
            with patch.dict('os.environ',{'STUDIO_FFMPEG':ffmpeg}):
                job=CloudRenderJob(obj,job_id,scratch);result=job.run()
            self.assertEqual(result['status'],'COMPLETE');self.assertEqual(len(result['result']['parts']),2)
            self.assertAlmostEqual(result['result']['duration'],2,delta=.15)
            self.assertTrue(all(x['chapterAligned'] for x in result['result']['parts']))
            for output in [result['result'],*result['result']['parts']]:
                decoded=subprocess.run([ffmpeg,'-v','error','-i',str(job.store.asset(p['id'],output['path'])),
                    '-f','null','-'],capture_output=True,check=True)
                self.assertEqual(decoded.stderr,b'')
            self.assertEqual({name:sha(folder/name) for name in original_hashes},original_hashes)
            self.assertFalse((folder/result['result']['path']).exists())


if __name__=='__main__':unittest.main()

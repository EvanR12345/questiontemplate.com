import copy, json, tempfile, unittest, hashlib, wave, math, os, subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from array import array
from pathlib import Path
from PIL import Image
from studio_data import ProjectStore,new_project
from studio_storage import R2Archive,sha
from studio_engagement import schedule,ding,audio_signature
from studio_thumbnails import make
from studio_render import VideoRenderer

class Objects:
    def __init__(self):self.data={};self.serial=0;self.fail=False;self.uploads=0
    def read(self,k):return self.data.get(k,(None,None))
    def put_json(self,k,value,etag=None,create=False):
        if create and k in self.data or etag and self.data.get(k,(None,None))[1]!=etag:raise ValueError('Conflict')
        self.serial+=1;self.data[k]=(json.dumps(value).encode(),str(self.serial))
    def upload(self,k,path,checksum):
        if self.fail:raise RuntimeError('secret must not be exposed')
        assert sha(path)==checksum
        self.data[k]=(path.read_bytes(),'asset');self.uploads+=1
    def download(self,k,path):path.write_bytes(self.data[k][0])
    def keys(self,prefix):return [k for k in self.data if k.startswith(prefix)]
    def url(self,k):return 'https://private.example/'+k

class CloudSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=ProjectStore(self.tmp.name)
        self.p=self.store.save(new_project('Cloud story'));self.obj=Objects()
        self.a=R2Archive(self.store,Path(self.tmp.name)/'absent');self.a.close()
        self.a.objects=self.obj;self.a.enabled=True;self.store.archive=self.a
        self.image=self.store.folder(self.p['id'])/'art.png';Image.new('RGB',(640,360),'purple').save(self.image)
    def tearDown(self):self.tmp.cleanup()
    def test_uploads_before_manifest_and_does_not_repeat_unchanged_assets(self):
        self.a.sync(self.p['id']);n=self.obj.uploads
        self.a.sync(self.p['id']);self.assertEqual(self.obj.uploads,n)
        self.assertTrue(self.image.exists());self.assertEqual(self.a.status()['projects'][self.p['id']]['status'],'SYNCED')
        self.assertEqual(self.a.files(self.p['id'])['files'][0]['sha256'],sha(self.image))
    def test_failed_upload_leaves_previous_cloud_snapshot_and_local_edits(self):
        self.a.sync(self.p['id']);key='studio/manifests/'+self.p['id']+'.json';old=self.obj.data[key]
        self.store.mutate(self.p['id'],lambda p:p.update(name='Keep me'))
        Image.new('RGB',(640,360),'red').save(self.image);self.obj.fail=True
        with self.assertRaises(RuntimeError):self.a.sync(self.p['id'])
        self.assertEqual(self.obj.data[key],old);self.assertEqual(self.store.load(self.p['id'])['name'],'Keep me')
    def test_dashboard_preserves_pending_files_and_does_not_label_local_edits_cloud_saved(self):
        before={r['path']:r for r in self.a.files(self.p['id'])['files']}
        self.assertIn('art.png',before);self.assertFalse(before['art.png']['cloud'])
        self.a.sync(self.p['id'])
        Image.new('RGB',(640,360),'red').save(self.image)
        added=self.store.folder(self.p['id'])/'new.wav';added.write_bytes(b'new pending narration')
        hidden=self.store.folder(self.p['id'])/'working';hidden.mkdir();(hidden/'private.json').write_text('{}')
        pending={r['path']:r for r in self.a.files(self.p['id'])['files']}
        self.assertFalse(pending['art.png']['cloud']);self.assertFalse(pending['new.wav']['cloud'])
        self.assertNotIn('working/private.json',pending)
        self.a.sync(self.p['id'])
        self.assertTrue(all(r['cloud'] for r in self.a.files(self.p['id'])['files']))
    def test_restore_rejects_newer_local_work_and_recovers_missing_asset(self):
        self.a.sync(self.p['id']);expected=self.image.read_bytes();self.image.unlink()
        self.assertEqual(self.store.asset(self.p['id'],'art.png').read_bytes(),expected)
        self.store.mutate(self.p['id'],lambda p:p.update(name='Newer'))
        with self.assertRaisesRegex(ValueError,'Both copies retained'):self.a.restore(self.p['id'])
    def test_complete_restart_restores_project_from_cloud(self):
        self.a.sync(self.p['id'])
        with tempfile.TemporaryDirectory() as new:
            store=ProjectStore(new);a=R2Archive(store,Path(new)/'absent');a.close();a.enabled=True;a.objects=self.obj;store.archive=a
            p=store.load(self.p['id']);self.assertEqual(p['name'],'Cloud story')
            self.assertEqual(sha(store.asset(p['id'],'art.png')),sha(self.image))
    def test_thumbnail_has_part_badge_and_is_under_youtube_limit(self):
        asset=make(self.store,self.p['id'],{'source':'art.png','title':'The return of magic','part':'1–6'})
        path=self.store.asset(self.p['id'],asset['path'])
        with Image.open(path) as img:self.assertEqual(img.size,(1280,720));self.assertGreater(img.getpixel((38,55))[0],180)
        self.assertLess(path.stat().st_size,2_000_000)
    def test_parallel_asset_requests_restore_once_without_overwriting_each_other(self):
        self.a.sync(self.p['id']);expected=self.image.read_bytes();self.image.unlink()
        entered=threading.Event();release=threading.Event();downloads=[]
        original=self.obj.download
        def delayed(key,path):
            downloads.append(key);entered.set()
            if not release.wait(5):raise RuntimeError('Test release timed out')
            original(key,path)
        self.obj.download=delayed
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.a.fetch_asset,self.p['id'],'art.png')
            self.assertTrue(entered.wait(5))
            second=pool.submit(self.a.fetch_asset,self.p['id'],'art.png')
            release.set()
            for request in (first,second):self.assertEqual(request.result(timeout=5).read_bytes(),expected)
        self.assertEqual(len(downloads),1)
    def test_asset_record_must_match_exact_path_not_only_project_hash_prefix(self):
        self.a.sync(self.p['id']);self.image.unlink()
        manifest,etag=self.a.manifest(self.p['id'])
        manifest['files']['art.png']['key']=manifest['files']['art.png']['key'].replace('art.png','other.png')
        with self.assertRaisesRegex(ValueError,'Invalid cloud asset identity'):self.a.fetch_asset(self.p['id'],'art.png')
        with self.assertRaisesRegex(ValueError,'Invalid cloud asset identity'):self.a.media_url(self.p['id'],'art.png')
    def test_project_catalogue_reuses_reads_and_returns_independent_values(self):
        self.a.sync(self.p['id']);calls=[];original=self.obj.keys
        def keys(prefix):calls.append(prefix);return original(prefix)
        self.obj.keys=keys
        first=self.a.list_projects();first[0]['name']='Do not leak edits'
        self.assertEqual(self.a.list_projects()[0]['name'],'Cloud story');self.assertEqual(len(calls),1)
        self.store.mutate(self.p['id'],lambda p:p.update(name='Changed'));self.a.sync(self.p['id'])
        self.assertEqual(self.a.list_projects()[0]['name'],'Changed');self.assertEqual(len(calls),2)

class EngagementTests(unittest.TestCase):
    def test_reminders_stable_across_outro_asset_and_thumbnail_changes(self):
        p=new_project();p['settings']['engagement']['popupEnabled']=True
        first=schedule(p,7200);self.assertGreater(len(first),5)
        previous=0
        for e in first:self.assertTrue(599<=e['start']-previous<=901);previous=e['start']
        p['settings']['engagement']['outroAudioPath']='outro.wav';p['thumbnails']=[{'path':'x.jpg'}]
        self.assertEqual(first,schedule(p,7200))
    def test_ding_volume_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'ding.wav';ding(target,.12)
            with wave.open(str(target)) as wav:
                self.assertEqual(wav.getframerate(),24000);x=array('h',wav.readframes(wav.getnframes()))
            self.assertLessEqual(max(abs(v) for v in x),32767*.12)

@unittest.skipUnless(os.environ.get('STUDIO_TEST_FFMPEG'),'Set STUDIO_TEST_FFMPEG for the real render test')
class RenderIntegrationTests(unittest.TestCase):
    def test_sixty_fps_reminder_ding_outro_and_safe_video_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ProjectStore(tmp);p=store.save(new_project());p['settings']['video'].update(width=640,height=360,fps=60)
            p['settings']['engagement'].update(popupEnabled=True,minMinutes=1,maxMinutes=1,outroEnabled=True,outroAudioPath='outro.wav',outroDuration=5)
            p['settings']['engagement']['outroAudioSignature']=audio_signature(p)
            p['settings']['watermark']={'enabled':True,'type':'text','text':'Studio'}
            folder=store.folder(p['id']);audio=folder/'outro.wav'
            with wave.open(str(audio),'wb') as wav:
                wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(b'\0'*24000*2)
            before=sha(audio);r=VideoRenderer(store,{'ffmpeg':os.environ['STUDIO_TEST_FFMPEG']})
            gate=lambda *a:None
            r.run(['-f','lavfi','-i','color=c=purple:s=640x360:r=60:d=70','-f','lavfi','-i','sine=frequency=200:sample_rate=24000:duration=70',*r.encoding(p),'-g','120','-c:a','aac','-ac','1','-shortest',str(folder/'source.mp4')],gate,folder/'fixture.log')
            result=r.engagement_export(p,{'path':'source.mp4','duration':70,'signature':'fixture'},gate)
            self.assertEqual(result['duration'],75);self.assertEqual(len(result['engagementEvents']),1);self.assertEqual(sha(audio),before)
            self.assertEqual(len(list(folder.glob('watermarked-*.mp4'))),0)
            self.assertTrue(result['watermarkIdentity']['settings']['enabled'])
            parts=r.split_export(p,result,gate,1);self.assertEqual(len(parts),2)
            self.assertAlmostEqual(sum(x['duration'] for x in parts),75,delta=.2)
            for part in parts:self.assertTrue(store.asset(p['id'],part['path']).is_file())
            decoded=subprocess.run([os.environ['STUDIO_TEST_FFMPEG'],'-hide_banner','-i',str(store.asset(p['id'],result['path'])),'-f','null','-'],check=True,capture_output=True)
            self.assertIn('60 fps',decoded.stderr.decode(errors='replace'))

if __name__=='__main__':unittest.main()

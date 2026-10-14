import io,json,tempfile,unittest,zipfile
import subprocess,sys
from pathlib import Path
from types import SimpleNamespace
from studio_cloud_pod_render import CloudPodRenderer,render_assets,SOURCES,control_packet,cloud_result
from studio_remote_render_worker import safe_url,receive,validate_video
from unittest.mock import patch
from studio_data import new_project

class RenderOnlyTest(unittest.TestCase):
    def test_real_chapter_trim_is_not_mistaken_for_a_different_output_format(self):
        video={'width':1280,'height':720,'avg_frame_rate':str(30.003045376103948),'nb_frames':'2463'}
        self.assertAlmostEqual(validate_video(video,82.0916666667,82.05,2),30.003045376103948)
        for wrong in (video|{'avg_frame_rate':'60/1'},video|{'nb_frames':'2500'},video|{'width':1920}):
            with self.assertRaises(ValueError):validate_video(wrong,82.0916666667,82.05,2)
        with self.assertRaises(ValueError):validate_video(video,83,82.05,2)
    def test_native_encoder_logging_cannot_corrupt_the_json_control_stream(self):
        result=subprocess.run([sys.executable,'-c',
            'import os;import studio_remote_render_worker as w;w.isolate_telemetry();os.write(1,b"[INFO] native encoder log\\n");w.emit({"event":"progress","stage":"Rendering"})'],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'event':'progress','stage':'Rendering'})
        self.assertIn('[INFO] native encoder log',result.stderr)
    def test_new_owned_allocations_get_distinct_pins_but_reconnect_keeps_same_pin(self):
        renderer=CloudPodRenderer(None,None,{'runpodSSHKeyFile':'dedicated-key'},lambda *_:None,Path('rental.json'))
        pod={'id':'owned-1','ssh':{'direct':{'host':'192.0.2.1','port':22,'username':'root'}}}
        first=renderer.ssh_args(pod);self.assertEqual(first,renderer.ssh_args(pod))
        self.assertNotEqual(first,renderer.ssh_args(pod|{'id':'owned-2'}))
        self.assertIn('StrictHostKeyChecking=accept-new',first)

    def test_bundle_identity_is_stable_for_unchanged_source(self):
        renderer=CloudPodRenderer(None,None,{},lambda *_:None,Path('unused'))
        self.assertEqual(renderer.bundle(),renderer.bundle())

    def test_cloud_parts_use_verified_namespaced_paths_without_changing_original_result(self):
        original={'path':'full.mp4','parts':[{'path':'part-1.mp4'}],'chapters':[{'path':'chapter-1.mp4'}],
            'narrationRender':{'path':'chapter-1.mp4'}}
        records={name:{'path':'cloud-renders/unique/'+name} for name in ('full.mp4','part-1.mp4','chapter-1.mp4')}
        result=cloud_result(original,records,'lium',12)
        self.assertEqual(result['parts'][0]['path'],'cloud-renders/unique/part-1.mp4')
        self.assertEqual(result['chapters'][0]['renderLocation'],'lium')
        self.assertEqual(result['narrationRender']['path'],'cloud-renders/unique/chapter-1.mp4')
        self.assertEqual(original['path'],'full.mp4')
        with self.assertRaisesRegex(ValueError,'verified cloud'):cloud_result(original,{},'lium',12)

    def test_isolated_render_bundle_can_validate_staged_projects_without_api_modules(self):
        p=new_project();p['settings']['director'].update(provider='openai-luna',executionMode='staged-lean',parallelism=4)
        p['settings']['image']['provider']='comfyui';p['settings']['budget']={'openaiUSD':1}
        renderer=CloudPodRenderer(None,None,{},lambda *_:None,Path('unused'))
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(io.BytesIO(renderer.bundle())) as bundle:bundle.extractall(folder)
            result=subprocess.run([sys.executable,'-c','import sys,json;from studio_data import validate_project;validate_project(json.load(sys.stdin));print("valid")'],
                input=json.dumps(p),text=True,capture_output=True,cwd=folder,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'valid')
    def test_changed_local_source_is_rejected_before_rental_and_cloud_only_source_is_not_fetched(self):
        p=new_project();p['chapters'][0]['sourceText']='Saved story';p['chapters'][0]['audio']={'path':'voice.wav'}
        record={'sha256':'a'*64,'bytes':3,'key':'private-object'}
        archive=SimpleNamespace(manifest=lambda *_:(dict(project=p),'etag'),asset_record=lambda *_:record,
            objects=SimpleNamespace(url=lambda *_:'https://storage.googleapis.com/object'))
        with tempfile.TemporaryDirectory() as folder:
            local=Path(folder)/'voice.wav';local.write_bytes(b'new')
            store=SimpleNamespace(asset_local=lambda *_:local)
            renderer=CloudPodRenderer(store,archive,{},lambda *_:None,Path(folder)/'lease.json')
            with self.assertRaisesRegex(ValueError,'differs'):renderer.capabilities(p)
            local.unlink()
            assets,_=renderer.capabilities(p)
            self.assertEqual(len(assets),1);self.assertFalse(local.exists())

    def test_long_project_control_is_compressed_and_checksum_verified_without_video_bytes(self):
        value={'project':{'chapters':[{'sourceText':'Story facts. '*2000,'shots':[{'prompt':'A grounded illustration. '*100} for _ in range(50)]} for _ in range(80)]}}
        packet=control_packet(value)
        self.assertLess(len(packet),100000)
        with patch('sys.stdin',io.StringIO(packet)):self.assertEqual(receive(),value)
        broken=json.loads(packet);broken['sha256']='0'*64
        with patch('sys.stdin',io.StringIO(json.dumps(broken)+'\n')):
            with self.assertRaisesRegex(ValueError,'identity'):receive()

    def test_bundle_contains_render_code_but_no_generation_or_credentials(self):
        renderer=CloudPodRenderer(None,None,{},lambda *_:None,Path('unused'))
        with zipfile.ZipFile(io.BytesIO(renderer.bundle())) as z:
            self.assertEqual(set(z.namelist()),set(SOURCES))
            self.assertNotIn('image_provider.py',z.namelist());self.assertNotIn('openai_director.py',z.namelist())
            self.assertNotIn('studio-config.json',z.namelist())
            for name in z.namelist():compile(z.read(name),name,'exec')

    def test_source_selection_reuses_media_and_excludes_existing_videos(self):
        p=new_project();ch=p['chapters'][0];ch['sourceText']='Saved story'
        ch['audio']={'path':'voice.wav'};ch['render']={'path':'old.mp4'}
        ch['scenes']=[{'shots':[{'imagePath':'saved.png','referenceImages':[{'path':'face.png'}]}]}]
        p['render']={'path':'old-full.mp4'}
        self.assertEqual(render_assets(p),['face.png','saved.png','voice.wav'])

    def test_only_google_object_capabilities_are_accepted(self):
        for value in ('http://storage.googleapis.com/a','https://evil.test/a','https://user:password@storage.googleapis.com/a'):
            with self.assertRaises(ValueError):safe_url(value)
        self.assertEqual(safe_url('https://storage.googleapis.com/private/object?signature=temporary'),
                         'https://storage.googleapis.com/private/object?signature=temporary')

    def test_cloud_outputs_use_a_unique_namespace_and_cannot_replace_old_selected_files(self):
        calls=[]
        class Blob:
            def generate_signed_url(self,**kw):calls.append(kw);return 'https://storage.googleapis.com/private/new'
        bucket=SimpleNamespace(get_blob=lambda *_args,**_kw:None,blob=lambda key:(calls.append(key) or Blob()))
        store=SimpleNamespace(asset_local=lambda _pid,path:Path(path))
        renderer=CloudPodRenderer(store,SimpleNamespace(objects=SimpleNamespace(bucket=bucket)),{},lambda *_:None,Path('unused'))
        renderer.upload_prefix='cloud-renders/'+'a'*32
        reply=renderer.upload_capability('pr-'+'1'*16,{'path':'story-old.mp4','bytes':123,'sha256':'b'*64})
        self.assertIn('/cloud-renders/'+'a'*32+'/',calls[0])
        self.assertEqual(reply['headers']['x-goog-if-generation-match'],'0')
        self.assertEqual(reply['headers']['x-goog-meta-sha256'],'b'*64)
        self.assertEqual(renderer.uploaded['story-old.mp4']['path'],renderer.upload_prefix+'/story-old.mp4')

if __name__=='__main__':unittest.main()

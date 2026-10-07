"""Settings exercise real dispatch guards and export caches, with no cloud calls."""
import base64, copy, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from studio_data import new_project, ProjectStore, get_shot
from studio_qc import check_level, should_check, validate_checks, review_signature, accept_image, decision, automatic_repairs
from studio_branding import validate, identity, bitmap
from studio_service import StudioService
from studio_render import VideoRenderer
from studio_overlap import validate_overlap
import full_video_test as full_fixtures
import server_test as server_fixtures

class DispatchSettingsTest(unittest.TestCase):
    setUp = full_fixtures.FullVideoTest.setUp
    tearDown = full_fixtures.FullVideoTest.tearDown
    # Reuse the actual orchestration harness, not its image-generation override.
    def test_explicit_off_overrides_max_and_deferred_generation_without_paid_qc(self):
        self.service.prepare_story(self.project['id'], {})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0];shot=ch['scenes'][0]['shots'][0]
        p['settings'].update(qcCheckLevel='off', visionQC=True, generationMode='MAX QUALITY')
        p=self.service.store.save(p)
        provider=self.service.providers['existing']
        self.service.providers['native-flux']=Mock()
        provider.generateImage=Mock(return_value={'pil':Image.new('RGB',(128,128),'navy')})
        provider.getCapabilities=lambda:{'maxReferenceImages':0,'supportsImageEditing':False}
        self.service.visual_check=Mock(side_effect=AssertionError('No paid QC allowed'))
        self.service.record_timing=lambda *a:None
        StudioService.generate(self.service,p,ch['id'],shot['id'],123,{'deferQC':True})
        actual=get_shot(self.service.store.load(p['id']),ch['id'],shot['id'])
        self.assertEqual(actual['qc']['status'],'UNCHECKED');self.assertIsNone(actual['qc']['pass'])
        self.service.visual_check.assert_not_called();self.assertTrue(actual['imagePath'])
        with self.assertRaisesRegex(ValueError,'checks are Off'):
            self.service.enqueue(p['id'],ch['id'],'qc',[shot['id']],{})
        with self.assertRaisesRegex(ValueError,'checks are Off'):
            StudioService.inspect_shot(self.service,p,ch['id'],shot['id'])
        self.assertFalse(automatic_repairs(p['settings'] | {'qcPolicy':'strict','automaticRepair':True}))

    def test_sampled_generation_checks_selected_shots_only_and_records_skips_honestly(self):
        self.service.prepare_story(self.project['id'], {})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0];first=ch['scenes'][0]['shots'][0]
        p['settings'].update(qcCheckLevel='sampled',qcSampleEvery=5)
        other=copy.deepcopy(first)
        for i in range(100):
            other['id']='sample-candidate-'+str(i)
            if not should_check(p,other):break
        first['end']=2;other.update(start=2,end=4)
        ch['scenes'][0]['shots'].append(other);p=self.service.store.save(p)
        provider=self.service.providers['existing'];self.service.providers['native-flux']=Mock()
        provider.generateImage=Mock(side_effect=lambda *a:{'pil':Image.new('RGB',(128,128),'navy')})
        provider.getCapabilities=lambda:{'maxReferenceImages':0,'supportsImageEditing':False}
        self.service.visual_check=Mock(return_value={'pass':True,'issues':[],'action':'accept'})
        self.service.record_timing=lambda *a:None
        StudioService.generate(self.service,p,ch['id'],first['id'],123,{})
        StudioService.generate(self.service,self.service.store.load(p['id']),ch['id'],other['id'],124,{})
        self.assertEqual(self.service.visual_check.call_count,1)
        actual=self.service.store.load(p['id'])
        self.assertTrue(get_shot(actual,ch['id'],first['id'])['qc']['pass'])
        self.assertEqual(get_shot(actual,ch['id'],other['id'])['qc']['status'],'UNCHECKED')
        self.assertIsNone(get_shot(actual,ch['id'],other['id'])['qc']['pass'])

class SettingsContractTest(unittest.TestCase):
    def test_stable_sampling_new_defaults_and_legacy_compatibility(self):
        p=new_project();self.assertEqual(check_level(p['settings']),'off')
        p['settings']['generationMode']='MAX QUALITY';self.assertEqual(check_level(p['settings']),'practical')
        p['settings']['qcCheckLevel']='off';self.assertFalse(should_check(p,{'id':'a'}))
        p['settings'].update(qcCheckLevel='sampled',qcSampleEvery=5)
        ch=p['chapters'][0];shots=[{'id':f'shot-{i}'} for i in range(200)]
        ch['scenes']=[{'shots':shots}];p['intro']['shots']=[{'id':'intro-first'},{'id':'intro-next'}]
        selected=[s['id'] for s in shots if should_check(p,s)]
        self.assertIn('shot-0',selected);self.assertTrue(should_check(p,p['intro']['shots'][0]))
        self.assertEqual(selected,[s['id'] for s in copy.deepcopy(shots) if should_check(copy.deepcopy(p),s)])
        self.assertTrue(20<len(selected)<70)
        for bad in [True,1,21,5.5,float('nan')]:
            with self.assertRaises(ValueError):validate_checks({'qcSampleEvery':bad})

    def test_manual_acceptance_survives_frequency_and_watermark_edits_without_faking_ai_pass(self):
        from studio_qc_test import PracticalQCTest
        fixture=PracticalQCTest();fixture.setUp()
        try:
            p=fixture.accept();s=get_shot(p,fixture.chid,fixture.sid);original=copy.deepcopy(s['qc'])
            p['settings'].update(qcCheckLevel='strict',qcSampleEvery=10,watermark={'enabled':True,'text':'Author'})
            self.assertEqual(decision(p,s,fixture.store)['disposition'],'accepted')
            self.assertEqual(s['qc'],original);self.assertFalse(s['qc']['pass'])
            fixture.store.save(p);reloaded=fixture.store.load(p['id'])
            self.assertEqual(reloaded['settings']['qcCheckLevel'],'strict')
            self.assertEqual(reloaded['settings']['watermark']['text'],'Author')
        finally:fixture.tearDown()

    def test_watermark_inputs_are_validated_and_logo_byte_changes_invalidate_export_publication(self):
        for w in [{'opacity':float('nan')},{'enabled':True,'text':''},{'imagePath':'../x'},
                  {'imagePath':'C:\\private.png'},{'color':"white;movie=x"},{'text':'A\nB'}, {'widthPercent':True}]:
            with self.assertRaises(ValueError):validate(w)
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project());logo=store.asset(p['id'],'logo.png')
            Image.new('RGBA',(40,20),(255,0,0,128)).save(logo)
            p['settings']['watermark']={'enabled':True,'type':'image','imagePath':'logo.png','opacity':.5}
            service=StudioService.__new__(StudioService);service.store=store;service.renderer=VideoRenderer(store,{})
            a=service.render_submission_signature(p);i=service.intro_submission_signature(p)
            dest=store.asset(p['id'],'prepared.png');bitmap(p,store,dest)
            with Image.open(dest) as img:self.assertEqual(img.getchannel('A').getextrema(),(64,64))
            Image.new('RGBA',(40,20),(0,255,0,128)).save(logo)
            self.assertNotEqual(a,service.render_submission_signature(p));self.assertNotEqual(i,service.intro_submission_signature(p))
            before=service.render_submission_signature(p);p['settings']['watermark']['enabled']=False
            self.assertNotEqual(before,service.render_submission_signature(p))

class SettingsBridgeTest(unittest.TestCase):
    setUp = server_fixtures.BridgeTest.setUp
    tearDown = server_fixtures.BridgeTest.tearDown
    request = server_fixtures.BridgeTest.request
    def test_logo_upload_preserves_alpha_and_settings_invalidate_exports_without_changing_sources(self):
        p=self.server.RequestHandlerClass.studio_service.store.save(new_project())
        p['intro']['duration']=30;p['chapters'][0]['sourceText']='Preserve this source.'
        service=self.server.RequestHandlerClass.studio_service;service.store.save(p)
        buf=io.BytesIO();Image.new('RGBA',(80,40),(255,255,255,96)).save(buf,'PNG')
        code,_,raw=self.request('/studio/upload',{'project':p['id'],'purpose':'watermark','data':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(code,200);path=json.loads(raw)['path']
        with Image.open(service.store.asset(p['id'],path)) as logo:self.assertEqual(logo.getchannel('A').getextrema(),(96,96))
        s=copy.deepcopy(p['settings']);s.update(qcCheckLevel='off',watermark={'enabled':True,'type':'image','imagePath':path})
        code,_,raw=self.request('/studio/edit',{'project':p['id'],'scope':'project','patch':{'settings':s}})
        self.assertEqual(code,200);actual=service.store.load(p['id'])
        self.assertTrue(actual['renderStale']);self.assertTrue(actual['chapters'][0]['renderStale']);self.assertTrue(actual['intro']['renderStale'])
        self.assertEqual(actual['chapters'][0]['sourceText'],'Preserve this source.');self.assertEqual(actual['intro']['duration'],30)
        s['watermark']['imagePath']='missing.png'
        code,_,_=self.request('/studio/edit',{'project':p['id'],'scope':'project','patch':{'settings':s}})
        self.assertEqual(code,400);self.assertEqual(service.store.load(p['id'])['settings']['watermark']['imagePath'],path)

if __name__=='__main__':unittest.main()

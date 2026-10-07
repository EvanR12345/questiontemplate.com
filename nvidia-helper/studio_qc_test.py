import copy, hashlib, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore, new_project, character, get_shot
from studio_qc import decision, production_status, accept_image, project_view, automatic_repairs, repair_budget_reason, review_signature
from studio_render import VideoRenderer

class PracticalQCTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=ProjectStore(self.temp.name)
        p=self.store.save(new_project());ch=p['chapters'][0];self.pid=p['id'];self.chid=ch['id'];self.sid='fixture-shot'
        self.store.asset(self.pid,'image.png').write_bytes(b'fixed image bytes')
        self.store.asset(self.pid,'voice.wav').write_bytes(b'fixed audio bytes')
        shot={'id':self.sid,'chapterId':self.chid,'sceneId':'fixture-scene','start':0,'end':1,
            'status':'FAILED','imagePath':'image.png','prompt':'A man holds a pistol.', 'negativePrompt':'',
            'imageProvider':'comfyui','imageModel':'klein','workflow':'klein','camera':{'shot':'close-up'},
            'characters':[],'generationSettings':{'seed':123},'motion':'static','manual':{},
            'qc':{'pass':False,'status':'FAILED','action':'image_edit','issues':['The framing is wider than requested.']}}
        ch.update(sourceText='A man holds a pistol.',cleanNarrationText='A man holds a pistol.',
            audio={'path':'voice.wav','duration':1},scenes=[{'id':'fixture-scene','shots':[shot]}])
        self.p=self.store.save(p);self.shot=get_shot(self.p,self.chid,self.sid)
    def tearDown(self):self.temp.cleanup()
    def accept(self,**extra):
        current=self.store.load(self.pid);shot=get_shot(current,self.chid,self.sid)
        return accept_image(self.store,self.pid,self.chid,self.sid,{'imagePath':'image.png',
            'reviewSpecSignature':review_signature(current,shot,self.store),**extra})
    def test_practical_minor_failure_is_advisory_without_rewriting_evidence(self):
        raw=copy.deepcopy(self.shot['qc']);result=decision(self.p,self.shot,self.store)
        self.assertEqual(result['disposition'],'advisory');self.assertFalse(result['blocking'])
        self.assertEqual(production_status(self.p,self.shot,self.store),'COMPLETE');self.assertEqual(self.shot['qc'],raw)
    def test_wrong_weapon_is_flagged_and_not_automatically_repaired(self):
        self.shot['qc']['issues']=['Christopher is holding a raised bladed weapon rather than the gun specified.']
        self.p['settings']['automaticRepair']=True
        self.assertTrue(decision(self.p,self.shot,self.store)['blocking'])
        self.assertFalse(automatic_repairs(self.p['settings']))
    def test_uncertain_legacy_claim_is_reviewable_not_silently_approved(self):
        self.shot['qc']['issues']=['Something might be wrong.']
        self.assertTrue(decision(self.p,self.shot,self.store)['blocking'])
    def test_structured_major_finding_outweighs_minor_keywords(self):
        issue='The framing shows the wrong weapon.'
        self.shot['qc'].update(issues=[issue],findings=[{'issue':issue,'severity':'major'}])
        self.assertTrue(decision(self.p,self.shot,self.store)['blocking'])
    def test_manual_acceptance_preserves_original_AI_flags_and_is_valid_in_strict_mode(self):
        raw=copy.deepcopy(self.shot['qc']);p=self.accept();s=get_shot(p,self.chid,self.sid)
        self.assertEqual(s['qc'],raw);self.assertEqual(s['qcDecision']['disposition'],'accepted')
        p['settings']['qcPolicy']='strict';self.assertFalse(decision(p,s,self.store)['blocking'])
        self.assertEqual(s['qcOverride']['reviewer'],'USER');self.assertEqual(s['status'],'COMPLETE')
    def test_changed_pixels_at_same_path_invalidate_user_acceptance(self):
        p=self.accept();s=get_shot(p,self.chid,self.sid);s['qc']['issues']=['Wrong weapon.']
        self.store.asset(self.pid,'image.png').write_bytes(b'new pixels')
        self.assertNotEqual(decision(p,s,self.store)['disposition'],'accepted')
        self.assertTrue(decision(p,s,self.store)['blocking'])
    def test_changed_story_or_camera_invalidates_user_acceptance(self):
        p=self.accept();s=get_shot(p,self.chid,self.sid)
        s['camera']['shot']='wide';self.assertNotEqual(decision(p,s,self.store)['disposition'],'accepted')
        p=self.accept();p['chapters'][0]['sourceText']='Different story.'
        self.assertNotEqual(decision(p,get_shot(p,self.chid,self.sid),self.store)['disposition'],'accepted')
    def test_changed_reference_bytes_invalidate_user_acceptance(self):
        person=character('Mira');person['references']=[{'path':'reference.png','kind':'face'}]
        self.store.asset(self.pid,'reference.png').write_bytes(b'original reference')
        self.p['characters']=[person];self.shot['characters']=[{'id':person['id'],'type':'main','appearanceState':{}}]
        self.store.save(self.p);p=self.accept();s=get_shot(p,self.chid,self.sid)
        self.store.asset(self.pid,'reference.png').write_bytes(b'changed reference')
        self.assertNotEqual(decision(p,s,self.store)['disposition'],'accepted')
    def test_stale_browser_acceptance_rejected_before_any_project_write(self):
        before=(self.store.folder(self.pid)/'project.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'selected image changed'):
            self.accept(imagePath='old.png')
        with self.assertRaisesRegex(ValueError,'image bytes changed'):
            self.accept(imageSHA256='wronghash')
        self.assertEqual((self.store.folder(self.pid)/'project.json').read_bytes(),before)
    def test_pending_QC_still_blocks_render_even_if_user_accepts(self):
        self.store.mutate(self.pid,lambda p:get_shot(p,self.chid,self.sid)['qc'].update({'status':'PENDING','pass':None}))
        p=self.accept();self.assertEqual(decision(p,get_shot(p,self.chid,self.sid),self.store)['disposition'],'pending')
    def test_read_only_project_view_does_not_mutate_old_flags_or_project_file(self):
        before=(self.store.folder(self.pid)/'project.json').read_bytes();p=project_view(self.store,self.pid)
        self.assertEqual(get_shot(p,self.chid,self.sid)['qcDecision']['disposition'],'advisory')
        self.assertEqual((self.store.folder(self.pid)/'project.json').read_bytes(),before)
    def test_stale_browser_spec_is_rejected_without_accepting_changed_camera(self):
        signature=review_signature(self.p,self.shot,self.store)
        self.store.mutate(self.pid,lambda p:get_shot(p,self.chid,self.sid)['camera'].update(shot='wide'))
        with self.assertRaisesRegex(ValueError,'reference inputs changed'):
            self.accept(reviewSpecSignature=signature)
        self.assertNotIn('qcOverride',get_shot(self.store.load(self.pid),self.chid,self.sid))
    def test_intro_acceptance_without_fictitious_chapter_and_script_change_invalidation(self):
        intro=copy.deepcopy(self.shot);intro.update(id='intro-fixture');intro.pop('chapterId')
        self.p['intro'].update(voiceText='Intro narration.',shots=[intro]);self.store.save(self.p)
        signature=review_signature(self.p,intro,self.store)
        p=accept_image(self.store,self.pid,None,'intro-fixture',{'imagePath':'image.png','reviewSpecSignature':signature})
        self.assertEqual(p['intro']['shots'][0]['qcDecision']['disposition'],'accepted')
        p['intro']['voiceText']='Changed intro narration.'
        self.assertNotEqual(decision(p,p['intro']['shots'][0],self.store)['disposition'],'accepted')
    def test_render_continues_advisory_and_rejects_clear_story_error_until_accepted(self):
        renderer=VideoRenderer(self.store,{})
        def write_output(args,*_):Path(args[-1]).write_bytes(b'encoded fixture')
        with patch.object(renderer,'run',side_effect=write_output) as run:
            renderer.chapter(self.p,self.p['chapters'][0],lambda *_:None);self.assertGreater(run.call_count,0)
        self.shot['qc']['issues']=['Wrong weapon.'];self.store.save(self.p)
        with patch.object(renderer,'run') as run:
            with self.assertRaisesRegex(ValueError,'needs review'):renderer.chapter(self.p,self.p['chapters'][0],lambda *_:None)
            run.assert_not_called()
        p=self.accept()
        with patch.object(renderer,'run',side_effect=write_output):renderer.chapter(p,p['chapters'][0],lambda *_:None)
    def test_strict_retries_require_combined_cost_evidence_inside_ten_percent(self):
        s={'qcPolicy':'strict','automaticRepair':True,'maxImageRetries':2}
        self.assertFalse(automatic_repairs(s))
        estimate={'baselineControlled':True,'includesAPI':True,'includesIncrementalRental':True,
            'includesRechecks':True,'baselineGenerationUSD':1,'combinedQCAndReplacementUpperUSD':.10,'maxImageRetriesCovered':2}
        s['qcCostEstimate']=estimate;self.assertTrue(automatic_repairs(s))
        estimate['combinedQCAndReplacementUpperUSD']=.10001;self.assertFalse(automatic_repairs(s))
        estimate['combinedQCAndReplacementUpperUSD']=.01;estimate['includesIncrementalRental']=False
        self.assertFalse(automatic_repairs(s))
    def test_cost_proof_rejects_nonfinite_values_and_uncovered_rechecks(self):
        s={'maxImageRetries':2,'qcCostEstimate':{'baselineControlled':True,'includesAPI':True,
            'includesIncrementalRental':True,'includesRechecks':True,'baselineGenerationUSD':float('nan'),
            'combinedQCAndReplacementUpperUSD':.01,'maxImageRetriesCovered':2}}
        self.assertIsNotNone(repair_budget_reason(s));s['qcCostEstimate']['baselineGenerationUSD']=1
        s['qcCostEstimate']['maxImageRetriesCovered']=1;self.assertIsNotNone(repair_budget_reason(s))
    def test_existing_flagged_image_cannot_buy_a_replacement_without_cost_proof(self):
        from studio_service import StudioService
        from unittest.mock import Mock
        service=StudioService.__new__(StudioService);service.store=self.store
        service.director=Mock();service.provider=Mock()
        with self.assertRaisesRegex(ValueError,'Repair cost is unverified'):
            service.generate(self.p,self.chid,self.sid,123,{})
        service.provider.assert_not_called()

if __name__=='__main__':unittest.main()

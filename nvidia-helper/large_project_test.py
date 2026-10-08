import copy, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore, new_project, new_chapter, character
from studio_qc import project_view, review_signature


class LargeProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=ProjectStore(self.temp.name)
        self.p=new_project('80 chapter fixture')
        person=character('Fixture person');person['references']=[{'path':'reference.png'}]
        self.p['characters']=[person]
        self.p['chapters']=[]
        for number in range(1,81):
            chapter=new_chapter(number);chapter['sourceText']='A recurring character walks through a room.'
            shots=[{'id':f'fixture-{number}-{n}','chapterId':chapter['id'],'sceneId':f'scene-{number}',
                    'characters':[{'id':person['id'],'type':'main','appearanceState':{}}],
                    'camera':{},'generationSettings':{},'imagePath':'image.png','prompt':'A person walks.','start':n,'end':n+1,'status':'COMPLETE',
                    'qc':{'pass':True}} for n in range(20)]
            chapter['scenes']=[{'id':f'scene-{number}','shots':shots}];self.p['chapters'].append(chapter)
        self.store.save(self.p)
        self.store.asset(self.p['id'],'reference.png').write_bytes(b'reference'*16000)
        self.store.asset(self.p['id'],'image.png').write_bytes(b'fixture image')

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_reference_is_read_once_per_view_but_changed_bytes_invalidate_next_view(self):
        original=Path.read_bytes;reads=[]
        def record(path):
            if path.name=='reference.png':reads.append(path)
            return original(path)
        with patch.object(Path,'read_bytes',record):
            result=project_view(self.store,self.p['id'])
        self.assertEqual(len(reads),1)
        self.assertEqual(sum(len(s['shots']) for c in result['chapters'] for s in c['scenes']),1600)
        for chapter in (result['chapters'][0],result['chapters'][-1]):
            shot=chapter['scenes'][0]['shots'][0]
            self.assertEqual(shot['qcReviewSignature'],review_signature(self.p,shot,self.store))
        previous=result['chapters'][0]['scenes'][0]['shots'][0]['qcReviewSignature']
        self.store.asset(self.p['id'],'reference.png').write_bytes(b'changed reference')
        fresh=project_view(self.store,self.p['id'])
        self.assertNotEqual(previous,fresh['chapters'][0]['scenes'][0]['shots'][0]['qcReviewSignature'])

    def test_project_picker_uses_saved_metadata_and_detects_external_changes(self):
        path=self.store.folder(self.p['id'])/'project.json'
        original=Path.read_text
        def forbid(path,*args,**kwargs):
            if path.name=='project.json':raise AssertionError('Picker reparsed whole project')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',forbid):
            a=self.store.list_local();a[0]['name']='Not the cached name'
            self.assertEqual(self.store.list_local()[0]['name'],'80 chapter fixture')
        value=json.loads(path.read_text());value['name']='Changed externally';value['revision']+=1
        path.write_text(json.dumps(value))
        self.assertEqual(self.store.list_local()[0]['name'],'Changed externally')

    def test_video_default_is_selected_720p30(self):
        video=new_project()['settings']['video']
        self.assertEqual((video['width'],video['height'],video['fps']),(1280,720,30))


if __name__=='__main__':unittest.main()

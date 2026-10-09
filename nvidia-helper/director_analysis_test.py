"""Actual multi-pass chapter analysis with deterministic streamed API replies."""
import copy,io,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from director_provider import validate_schema
from studio_data import character,new_project,new_chapter,ProjectStore
from studio_overlap import CloudStoryOverlap
from studio_service import StudioService


class PlanningProvider:
    id='comfyui'
    def unload(self):pass
    def planningCatalog(self):return {'installed':True,'models':['klein'],'workflow':['klein']}
    def getCapabilities(self):return {'promptFormat':'natural-language','supportsNegativePrompt':False,
        'maxReferenceImages':3,'supportsStyleReference':True}
    def validateSettings(self,settings,**kwargs):return copy.deepcopy(settings)
    def healthCheck(self):raise AssertionError('No image server in planning test')
    def generateImage(self,*args):raise AssertionError('No image purchases in planning test')


class PlanningService(StudioService):
    def worker(self):pass
    def check_cloud_budget(self,*args):pass


class ChapterDirectorAnalysisTest(unittest.TestCase):
    def fixture(self):
        p=new_project();p['chapters'].append(new_chapter(2))
        person=character('Mira');person['permanentIdentity']['naturalHair']='brown'
        person['defaultAppearance']['outfit']='gray coat';p['characters']=[person]
        for chapter in p['chapters']:
            text=['Mira takes the brass key in her right hand.' if chapter['number']==1 else
                'Mira changes into a red coat while keeping the key.',
                'Mira enters the school hallway.']
            text += ['Mira carries the key past doorway '+str(i)+'.' for i in range(12)]
            chapter['sourceText']=' '.join(text)
            chapter['audio']={'duration':len(text)*5,'sentences':[
                {'index':i,'text':s,'start':i*5,'end':(i+1)*5} for i,s in enumerate(text)]}
        p['settings']['director'].update(provider='openai-luna',reasoning='Fast')
        p['settings']['budget']={'openaiUSD':1}
        p['settings']['image'].update(provider='comfyui',model='klein',workflow='klein')
        return p

    def run_plan(self,folder,initial,parallelism):
        seen=[]
        service=PlanningService(folder,None,lambda:None,threading.RLock(),lambda:None,lambda:None,
            type('Queue',(),{'snapshot':lambda self:{'current':None,'counts':{}}})())
        service.providers['comfyui']=PlanningProvider()
        p=copy.deepcopy(initial);p['settings']['director']['parallelism']=parallelism;p=service.store.save(p)
        overlap=CloudStoryOverlap(service,p,{'overlap':True})
        def response(request,**kwargs):
            body=json.loads(request.data);role=body['input'][0]['content']
            context=json.loads(body['input'][1]['content'][0]['text']);seen.append((role,context))
            if 'Casting supervisor' in role:value={'people':[]}
            elif 'Story analyst' in role:
                sentence=context['sentences'][0]['text'];cid=context['chapterCast'][0]['id']
                field,value=('outfit','red coat') if 'red coat' in sentence else ('key','right hand')
                value={'summary':'Mira carries the key through school.','people':[],
                    'locations':[{'name':'school hallway','description':'school hallway'}],
                    'beats':[{'sentence':i,'action':s['text'],'emotion':'calm'} for i,s in enumerate(context['sentences'])],
                    'changes':[{'characterId':cid,'field':field,'value':value,'sentence':0}],
                    'environmentChanges':[],'objects':['key'],'goals':['continue'],'unresolved':[]}
            elif 'Chapter director' in role:
                cid=context['people'][0]['id']
                value={'scenes':[{'startSentence':i,'endSentence':i,'purpose':'follow source moment',
                    'location':'school hallway','mood':'calm','characters':[cid],'shotCount':1,
                    'pacingReason':'one visible moment'} for i in range(len(context['sentences']))]}
            elif 'Scene director' in role:
                cid=context['people'][0]['id']
                value={'shots':[{'sceneIndex':i,'startSentence':i,'endSentence':i,'characters':[cid],
                    'action':s['text'],'expression':'calm','pose':'walking','lighting':'daylight',
                    'motion':'slow zoom in','transition':'cut'} for i,s in enumerate(context['sentences'])]}
            elif 'Cinematographer' in role:
                value={'cameras':[{'shotIndex':i,'shot':'medium wide','angle':'eye level',
                    'composition':'Mira foreground right'} for i in range(len(context['shots']))]}
            elif 'Workflow planner' in role:
                value={'provider':'comfyui','model':'klein','workflow':'klein',
                    'referenceStrategy':'canonical identities','reason':'configured compatible workflow'}
            elif 'Continuity supervisor' in role:value={'issues':[],'intentionalChanges':[],'objectChanges':[]}
            elif 'Image prompt engineer' in role:
                value={'prompts':[{'shotIndex':i,'prompt':'Side light emphasizes the current action.'}
                    for i in range(len(context['shots']))]}
            else:raise AssertionError('Unexpected additional director pass: '+role[:80])
            validate_schema(value,body['text']['format']['schema'])
            identity='r-'+str(len(seen))
            events=[{'type':'response.created','response':{'id':identity}},
                {'type':'response.output_text.delta','delta':json.dumps(value)},
                {'type':'response.completed','response':{'id':identity,'status':'completed',
                    'usage':{'input_tokens':100,'output_tokens':30}}}]
            return io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                for chapter in p['chapters']:
                    overlap.main.analyze(service.store.load(p['id']),chapter['id'],
                        {'offlinePlanning':True,'managedPipeline':True})
            saved=service.store.load(p['id'])
            result=[]
            for chapter in saved['chapters']:
                result.append({'shots':[{k:s[k] for k in ('start','end','characters','action','camera','prompt','motion','transition')}
                    for scene in chapter['scenes'] for s in scene['shots']],
                    'current':chapter['handoff']['state']['characters'][p['characters'][0]['id']]})
            return result,seen
        finally:overlap.close('COMPLETE');service.close()

    def test_parallel_and_serial_plans_keep_same_facts_timing_prompts_and_handoffs(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.fixture()
            serial,_=self.run_plan(Path(folder)/'serial',p,1)
            parallel,requests=self.run_plan(Path(folder)/'parallel',p,3)
            self.assertEqual(serial,parallel)
            cid=p['characters'][0]['id']
            analyses=[c for role,c in requests if 'Story analyst' in role]
            self.assertEqual(analyses[1]['priorState']['characters'][cid]['key'],'right hand')
            self.assertEqual(parallel[1]['current']['outfit'],'red coat')
            self.assertEqual(parallel[1]['current']['key'],'right hand')
            self.assertEqual(len(parallel[1]['shots']),14)
            self.assertIn('red coat',parallel[1]['shots'][0]['prompt'])
            self.assertIn('brown',parallel[1]['shots'][0]['prompt'])

if __name__=='__main__':unittest.main()

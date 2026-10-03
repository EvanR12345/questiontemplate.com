import copy,tempfile,unittest
from PIL import Image
from studio_data import ProjectStore,new_project,character
from studio_service import StudioService

class References(unittest.TestCase):
    def test_repair_binds_saved_identity_and_preserves_old_references(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=new_project('References')
            person=character('Jon');person['references']=[{'path':'source.png','kind':'face'},{'path':'identity.png','kind':'face'}]
            p['characters']=[person];p=store.save(p)
            for path in ('source.png','identity.png'):Image.new('RGB',(16,16),'red').save(store.asset(p['id'],path))
            class Provider:
                id='native-flux'
                def getCapabilities(self):return {'maxReferenceImages':2,'supportsImageEditing':True,'supportsStyleReference':True,'supportsNegativePrompt':False}
                def validateSettings(self,s):return copy.deepcopy(s)
                def generateImage(self,r,checkpoint):
                    self.request=r
                    return {'pil':Image.new('RGB',(16,16),'blue'),'settings':r['settings']}
            provider=Provider();service=StudioService.__new__(StudioService)
            service.store=store;service.unload_models=lambda:None;service.before_image=lambda:None
            service.provider=lambda _:provider;service.providers={'existing':type('Existing',(),{'unload':lambda _:None})()}
            service.checkpoint=lambda *args:None
            options={'characterId':person['id'],'repairReferencePath':'source.png','identityReferencePath':'identity.png','repairPrompt':'Remove an unwanted mark.'}
            service.character_reference(p,options)
            self.assertEqual(provider.request['operation'],'edit')
            self.assertTrue(provider.request['sourceImage'].startswith('data:image/png;base64,'))
            self.assertEqual(len(provider.request['referenceImages']),1)
            references=store.load(p['id'])['characters'][0]['references']
            self.assertEqual(references[1:],person['references'])
            self.assertEqual(references[0]['metadata']['referenceRoles'],[{'path':'source.png','role':'edit-source'},{'path':'identity.png','role':'identity'}])
            with self.assertRaisesRegex(ValueError,'this character'):
                service.character_reference(p,options | {'identityReferencePath':'some-other-character.png'})

if __name__=='__main__':unittest.main()

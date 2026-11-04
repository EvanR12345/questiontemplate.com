"""Additional body-layout evidence for crowded focal casts; raw first review retained."""
import copy
ROLE='Visual quality reviewer: Independently examine body topology in this ONE generated picture. Do not grade clothes, scars, pose accuracy or identity. Count the intended focal people, then trace each visible head to its own neck and torso and each visible leg through hips to that SAME person. Look carefully at seated and overlapping people: a pair of bare lap legs or bikini hips fused into a suited man is a disconnected body, even if the upper body of another woman is visible behind him. Follow the anatomy rather than accepting a plausible overall composition. A genuinely occluded person or limb is not evidence of a defect. Do not require background scene people who are not intended focal cast. Confirm a major defect only for an explicitly visible headless body fragment, fused separate people, duplicated torso/head or missing essential focal person supported by visible body evidence; minor hands or clothing differences are not structural defects. Report observable evidence with image location. If geometry is genuinely ambiguous use uncertain, not defect. Return a practical targeted correction only if confirmed.'

def context_for(context):
 facts=context.get('expectedShot',{}).get('subjectBoundFacts',[])
 images=context.get('_images',[])
 if len(facts)<3 or not images:return None
 return {'expectedShot':{'subjectBoundFacts':facts},
  'intendedFocalPeople':[{'name':f['name'],'description':f.get('description','')} for f in facts],
  'camera':context['expectedShot'].get('camera',{}),'_images':images[:1]}

def attach(result,review):
 result['bodyTopologyReview']=copy.deepcopy(review)
 if review['assessment']!='confirmed_defect':return
 if not review.get('evidence') or not review.get('repairPrompt','').strip():
  raise ValueError('A confirmed body-layout defect requires visible evidence and a targeted correction.')
 result.setdefault('criticalStoryBlockers',[]).append({'code':'BODY_DISCONNECTED','subjectId':'scene',
  'issue':' '.join(review['evidence']),'basis':'Independent body-layout review of the generated image without identity-reference distraction'})
 result['productionRepairPrompt']=((result.get('productionRepairPrompt') or result.get('repairPrompt',''))+' '+review['repairPrompt']).strip()

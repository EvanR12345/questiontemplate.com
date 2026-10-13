"""One explicitly named, conservative production director preset."""
import copy
import math


def recommended_settings(settings):
    result=copy.deepcopy(settings)
    cap=result.get('budget',{}).get('openaiUSD')
    if (result.get('director',{}).get('provider')=='openai-luna'
            and result.get('image',{}).get('provider')=='comfyui'
            and type(cap) in (int,float) and math.isfinite(cap) and cap>0):
        result['director'].update(executionMode='staged-lean',reasoning='Balanced',parallelism=4,
            processingTier='default',factGroupSentences=128,visualPacking='small',compactCuts=False,
            sourceVisualOverlap=False,factBeatsMode='analyst')
    result.update(unattendedProduction=True,appearanceHandling='Automatic')
    if result.get('qcCheckLevel')!='off':
        result.update(automaticRepair=True,qcRepairBudgetMode='project-cap')
    # No story fields, voice/style, image cadence/model, manual scene decisions,
    # reference selection, existing outputs or spending grants are modified.
    return result

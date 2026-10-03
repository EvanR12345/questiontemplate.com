"""FLUX.2 Klein distilled core-node workflows, alongside the Qwen fallback.

Graph structure follows Comfy-Org's official FLUX.2 Klein templates. Explicit
reference-count variants keep each VAE/ReferenceLatent chain fully connected.
"""
import json
import sys
from pathlib import Path
from qwen_workflow import bundle as qwen_bundle, node

MODEL = 'flux2-klein-4b'
CAPS = {
    'supportsMultipleReferences': True, 'supportsImageEditing': True,
    'supportsNegativePrompt': False, 'supportsSeed': True,
    'supportsLoRA': False, 'supportsStyleReference': True,
    'maxReferenceImages': 3, 'maxResolution': 2048,
    'recommendedResolution': [1344, 768], 'recommendedSteps': 4,
    'recommendedSampler': 'euler', 'recommendedGuidance': 1.0,
    'recommendedDenoisingStrength': 1.0, 'promptFormat': 'natural-language',
    'negativePromptFormat': 'unsupported',
    'hardwareRequirements': {'vramGB': 16, 'ramGB': 32},
    'referenceLimitations': 'Three total reference images including an edit source. '
        'Identity is conditioned through VAE reference latents, not a face-lock guarantee. '
        'Current clothing must be described explicitly; distilled CFG 1 uses no negative guidance.'
}


def variant(count):
    name = 'flux2-klein-text-4' if not count else f'flux2-klein-reference-{count}-4'
    g = {
        '1': node('UNETLoader', unet_name='flux-2-klein-4b-fp8.safetensors', weight_dtype='default'),
        '2': node('CLIPLoader', clip_name='qwen_3_4b.safetensors', type='flux2', device='default'),
        '3': node('VAELoader', vae_name='flux2-vae.safetensors'),
        '4': node('CLIPTextEncode', clip=['2', 0], text=''),
        '5': node('ConditioningZeroOut', conditioning=['4', 0]),
        '6': node('RandomNoise', noise_seed=42),
        '7': node('KSamplerSelect', sampler_name='euler'),
        '8': node('Flux2Scheduler', steps=4, width=1344, height=768),
        '9': node('EmptyFlux2LatentImage', width=1344, height=768, batch_size=1),
        '10': node('CFGGuider', model=['1', 0], positive=['4', 0], negative=['5', 0], cfg=1.0),
        '11': node('SamplerCustomAdvanced', noise=['6', 0], guider=['10', 0], sampler=['7', 0], sigmas=['8', 0], latent_image=['9', 0]),
        '12': node('VAEDecode', samples=['11', 0], vae=['3', 0]),
        '13': node('SaveImage', images=['12', 0], filename_prefix='QuestionTemplate/' + name),
    }
    bindings = {'prompt': [['4', 'text']], 'seed': [['6', 'noise_seed']],
        'width': [['8', 'width'], ['9', 'width']], 'height': [['8', 'height'], ['9', 'height']],
        'steps': [['8', 'steps']], 'sampler': [['7', 'sampler_name']], 'guidance': [['10', 'cfg']]}
    positive, negative = ['4', 0], ['5', 0]
    for index in range(count):
        load, scale, encode, pos, neg = map(str, range(20 + index * 5, 25 + index * 5))
        g[load] = node('LoadImage', image='reference-not-bound.png')
        g[scale] = node('ImageScaleToTotalPixels', image=[load, 0], upscale_method='lanczos', megapixels=1.0, resolution_steps=1)
        g[encode] = node('VAEEncode', pixels=[scale, 0], vae=['3', 0])
        g[pos] = node('ReferenceLatent', conditioning=positive, latent=[encode, 0])
        g[neg] = node('ReferenceLatent', conditioning=negative, latent=[encode, 0])
        positive, negative = [pos, 0], [neg, 0]
        bindings[f'reference{index}'] = [[load, 'image']]
    g['10']['inputs'].update(positive=positive, negative=negative)
    return {'name': name, 'model': MODEL, 'kind': 'reference' if count else 'text',
        'default': True, 'referenceCount': count, 'prompt': g, 'bindings': bindings,
        'referenceNodes': {str(i): str(20 + i * 5) for i in range(count)},
        'presetSettings': {'steps': 4, 'guidance': 1.0, 'sampler': 'euler',
            'scheduler': 'flux2', 'denoisingStrength': 1.0, 'loras': []}}


def bundle():
    result = qwen_bundle()
    result['version'] = 2
    result['variants'] += [variant(i) for i in range(4)]
    result['automaticWorkflows'] = {'flux2-klein-4b-auto': {
        'model': MODEL, 'referenceCreationWorkflow': 'flux2-klein-text-4',
        'referenceResolution': [1024, 1024]}}
    result['modelCapabilities'] = {MODEL: CAPS}
    return result


if __name__ == '__main__':
    Path(sys.argv[1]).write_text(json.dumps(bundle(), indent=2), encoding='utf-8')

"""Stable Diffusion on the existing CUDA environment, with validated decoding."""
import base64
import gc
import hashlib
import io
import secrets
import time
from collections import OrderedDict

from image_queue import validate_job

PRESETS = {
    'volume': {'steps': 8, 'guidance': 5.5},
    'fast': {'steps': 12, 'guidance': 6.0},
    'balanced': {'steps': 20, 'guidance': 7.0},
    'quality': {'steps': 30, 'guidance': 7.5},
}


class ImageEngine:
    def __init__(self, torch, device, index, gpu, model_id, inpaint_id):
        self.torch, self.device, self.index, self.gpu = torch, device, index, gpu
        self.model_id, self.inpaint_id = model_id, inpaint_id
        self.low_vram = torch.cuda.get_device_properties(index).total_memory < 5 * 1024**3
        self.pipe = None
        self.operation = None
        self.ip_loaded = False
        self.embeddings = OrderedDict()
        self.safe_unet = False
        self.safe_convolutions = 'GTX 16' in gpu

    def health(self):
        return {'ok': True, 'backend': 'cuda', 'gpu': self.gpu, 'model': self.model_id,
                'mode': '4 GB model offload + SDPA + float32 VAE' if self.low_vram else 'CUDA + float32 VAE',
                'sharedHelper': True, 'queue': True, 'presets': PRESETS}

    def unload(self):
        if self.pipe is not None:
            self.pipe.maybe_free_model_hooks()
            self.pipe.remove_all_hooks()
        self.pipe = None
        self.operation = None
        self.ip_loaded = False
        self.embeddings.clear()
        gc.collect()
        self.torch.cuda.empty_cache()

    def _configure(self):
        from diffusers import DPMSolverMultistepScheduler
        self.pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            self.pipe.scheduler.config, algorithm_type='dpmsolver++', solver_order=2, use_karras_sigmas=True)
        # PyTorch SDPA handles attention without the old per-head Python loop.
        if not self.ip_loaded:
            self.pipe.disable_attention_slicing()
        self.pipe.enable_vae_slicing()
        self.pipe.enable_vae_tiling()
        self.pipe.set_progress_bar_config(disable=True)
        if self.low_vram:
            self.pipe.enable_model_cpu_offload(gpu_id=self.index)
        else:
            self.pipe.to(self.device)

    def load(self, operation='generate', checkpoint=lambda *args: None):
        if self.pipe is not None and self.operation == operation:
            return
        from diffusers import StableDiffusionPipeline, StableDiffusionInpaintPipeline
        if self.pipe is not None:
            self.pipe.maybe_free_model_hooks()
            self.pipe.remove_all_hooks()
            self.pipe.to('cpu')
            cls = StableDiffusionInpaintPipeline if operation == 'inpaint' else StableDiffusionPipeline
            self.pipe = cls.from_pipe(self.pipe)
            self.operation = operation
            self._configure()
            self._safe_encode()
            return
        checkpoint(0, 0, 'Loading image model')
        # The SD 1.5 four-channel inpaint path shares the denoiser and VAE.
        # It blends unmasked latents each step, avoiding another model download.
        cls = StableDiffusionInpaintPipeline if operation == 'inpaint' else StableDiffusionPipeline
        dtype = self.torch.float32 if self.safe_unet else self.torch.float16
        options = dict(torch_dtype=dtype, safety_checker=None, requires_safety_checker=False)
        try:
            self.pipe = cls.from_pretrained(self.model_id, local_files_only=True, **options)
        except OSError:
            checkpoint(0, 0, 'Downloading missing image model files')
            self.pipe = cls.from_pretrained(self.model_id, **options)
        self.operation = operation
        self.pipe.vae.to(dtype=self.torch.float32)
        if self.safe_convolutions and dtype == self.torch.float16:
            self._upcast_convolutions()
        self._configure()
        self._safe_encode()
        checkpoint(0, 0, 'Model ready')

    def _safe_encode(self):
        if self.operation == 'inpaint':
            original = self.pipe._encode_vae_image
            def encode(image, generator):
                return original(image.float(), generator).to(dtype=self.pipe.unet.dtype)
            self.pipe._encode_vae_image = encode

    def _upcast_convolutions(self):
        # GTX 16xx cuDNN fp16 convolutions can produce NaNs at the first step.
        # Keep compact fp16 weights/attention, compute only convolutions in fp32.
        import types
        import torch.nn.functional as functional
        torch = self.torch
        def forward(module, inputs):
            dtype = inputs.dtype
            bias = module.bias.float() if module.bias is not None else None
            return functional.conv2d(inputs.float(), module.weight.float(), bias,
                                     module.stride, module.padding, module.dilation, module.groups).to(dtype)
        models = [self.pipe.unet, getattr(self.pipe, 'image_encoder', None)]
        for model in models:
            if model is not None:
                for module in model.modules():
                    if isinstance(module, torch.nn.Conv2d):
                        module.forward = types.MethodType(forward, module)

    def decode_image(self, value, mode='RGB'):
        from PIL import Image, ImageOps
        if not isinstance(value, str) or not value.startswith('data:image/'):
            raise ValueError('Use a valid image data URL.')
        try:
            image = Image.open(io.BytesIO(base64.b64decode(value.split(',', 1)[1], validate=True)))
            if image.width * image.height > 16_000_000:
                raise ValueError('Reference image exceeds 16 megapixels.')
            return ImageOps.exif_transpose(image).convert(mode)
        except Exception as error:
            raise ValueError('Could not read the reference/source/mask image: ' + str(error)) from error

    def collage(self, values):
        from PIL import Image, ImageOps
        images = [self.decode_image(value) for value in values]
        out = Image.new('RGB', (384 * len(images), 384), 'white')
        for index, image in enumerate(images):
            thumb = ImageOps.contain(image, (384, 384), Image.Resampling.LANCZOS)
            out.paste(thumb, (index * 384 + (384-thumb.width)//2, (384-thumb.height)//2))
        # CLIP center-crops; using a square canvas preserves every reference.
        square = Image.new('RGB', (out.width, out.width), 'white')
        square.paste(out, (0, (out.width-out.height)//2))
        return square

    def _reference(self, refs, strength, checkpoint):
        torch = self.torch
        if refs and not self.ip_loaded:
            checkpoint(0, 0, 'Loading character reference adapter')
            self.pipe.maybe_free_model_hooks()
            self.pipe.remove_all_hooks()
            self.pipe.to('cpu')
            torch.cuda.empty_cache()
            options = dict(subfolder='models', weight_name='ip-adapter_sd15.bin')
            try:
                self.pipe.load_ip_adapter('h94/IP-Adapter', local_files_only=True, **options)
            except OSError:
                checkpoint(0, 0, 'Downloading missing character adapter files')
                self.pipe.load_ip_adapter('h94/IP-Adapter', **options)
            self.ip_loaded = True
            if self.safe_convolutions:
                self._upcast_convolutions()
            self._configure()
        if not self.ip_loaded:
            return {}
        self.pipe.set_ip_adapter_scale(strength if refs else 0)
        cache_key = hashlib.sha256(''.join(refs).encode()).hexdigest()
        if refs and cache_key not in self.embeddings:
            with torch.inference_mode():
                values = self.pipe.prepare_ip_adapter_image_embeds(self.collage(refs), None, self.device, 1, True)
            if not all(torch.isfinite(value).all() for value in values):
                raise FloatingPointError('Character reference encoder produced invalid embeddings. Try a smaller, clear reference image.')
            self.embeddings[cache_key] = [value.cpu() for value in values]
            if len(self.embeddings) > 16:
                self.embeddings.popitem(last=False)
        if refs:
            self.embeddings.move_to_end(cache_key)
            values = self.embeddings[cache_key]
        elif self.embeddings:
            values = [torch.zeros_like(value) for value in next(iter(self.embeddings.values()))]
        else:
            with torch.inference_mode():
                from PIL import Image
                values = self.pipe.prepare_ip_adapter_image_embeds(Image.new('RGB', (384,384)), None, self.device, 1, True)
            values = [torch.zeros_like(value) for value in values]
        return {'ip_adapter_image_embeds': [value.to(self.device) for value in values]}

    def generate(self, data, checkpoint=lambda *args: None):
        data = validate_job(data)
        torch = self.torch
        operation = data.get('operation', 'generate')
        preset = PRESETS.get(data.get('preset'), PRESETS['balanced'])
        steps = int(data.get('steps', preset['steps']))
        seed = data.get('seed', secrets.randbelow(2**31 - 1))
        started = time.perf_counter()
        self.load(operation, checkpoint)
        refs = data.get('reference_images') or []
        kwargs = dict(prompt=data['prompt'], negative_prompt=str(data.get('negative', ''))[:4000],
                      width=int(data.get('width', 512))//8*8, height=int(data.get('height', 512))//8*8,
                      num_inference_steps=steps, guidance_scale=float(data.get('guidance', preset['guidance'])),
                      generator=torch.Generator(device=self.device).manual_seed(seed), output_type='latent')
        if operation == 'inpaint':
            from PIL import Image, ImageOps
            image = self.decode_image(data['image'])
            mask = self.decode_image(data['mask'], 'L')
            if mask.size != image.size:
                raise ValueError('Inpaint mask dimensions must match the source image. White = repaint; black = preserve.')
            if mask.getextrema()[1] == 0:
                raise ValueError('Mask is entirely black. Paint the area to change in white.')
            w, h = kwargs['width'], kwargs['height']
            kwargs.update(image=ImageOps.fit(image, (w, h), Image.Resampling.LANCZOS),
                          mask_image=ImageOps.fit(mask, (w, h), Image.Resampling.NEAREST), strength=1.0)
        kwargs.update(self._reference(refs if operation != 'inpaint' else [], float(data.get('reference_strength', .7)), checkpoint))
        def callback(pipe, index, timestep, values):
            if not torch.isfinite(values['latents']).all():
                raise FloatingPointError(f'UNet/scheduler produced invalid latents at step {index+1}; dtype={values["latents"].dtype}.')
            checkpoint(index+1, steps, 'Denoising')
            return values
        kwargs['callback_on_step_end'] = callback
        try:
            checkpoint(0, steps, 'Generating')
            with torch.inference_mode():
                latents = self.pipe(**kwargs).images
                checkpoint(steps, steps, 'Decoding in float32')
                # The denoiser is offloaded before the fp32 VAE runs on 4 GB GPUs.
                if self.low_vram and getattr(self.pipe, 'final_offload_hook', None):
                    self.pipe.final_offload_hook.offload()
                latents = latents.to(device=self.device, dtype=torch.float32)
                decoded = self.pipe.vae.decode(latents / self.pipe.vae.config.scaling_factor, return_dict=False)[0]
                if not torch.isfinite(decoded).all():
                    raise FloatingPointError('VAE returned NaN/Inf despite float32 decoding.')
                image = self.pipe.image_processor.postprocess(decoded, output_type='pil')[0]
                if operation == 'inpaint':
                    from PIL import Image
                    image = Image.composite(image, kwargs['image'], kwargs['mask_image'])
                import numpy as np
                pixels = np.asarray(image)
                if pixels.max() <= 2:
                    raise FloatingPointError('Decoded image is completely black; output rejected.')
                stats = {'min': int(pixels.min()), 'max': int(pixels.max()), 'mean': float(pixels.mean())}
            checkpoint(steps, steps, 'Image validated')
            return {'pil': image, 'seed': seed, 'seconds': time.perf_counter()-started, 'pixels': stats,
                    'precision': 'float32' if self.safe_unet else 'fp16 weights / fp32 convolutions and VAE' if self.safe_convolutions else 'fp16 UNet / fp32 VAE',
                    'preset': data.get('preset', 'balanced')}
        except FloatingPointError:
            if operation == 'generate' and not self.safe_unet:
                self.safe_unet = True
                self.pipe.maybe_free_model_hooks()
                self.pipe.remove_all_hooks()
                self.pipe.to('cpu')
                self.pipe.unet.to(dtype=torch.float32)
                self.pipe.text_encoder.to(dtype=torch.float32)
                self._configure()
                checkpoint(0, steps, 'Invalid fp16 latents; retrying same seed in float32')
                return self.generate({**data, 'seed': seed}, checkpoint)
            raise
        finally:
            if self.pipe is not None:
                self.pipe.maybe_free_model_hooks()
            torch.cuda.empty_cache()

    def legacy(self, data):
        result = self.generate(data)
        out = io.BytesIO()
        result.pop('pil').save(out, 'PNG', compress_level=2)
        result['image'] = 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()
        return result

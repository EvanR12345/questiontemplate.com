# QuestionTemplate Image Helper

Free local image generation for QuestionTemplate Manga Studio.

## Windows

1. Install Python 3.11 and a current NVIDIA driver.
2. Run `image-helper-windows.bat` from questiontemplate.com, or place this folder on your PC and run `start-windows.bat`.
3. The first setup installs CUDA PyTorch and downloads Stable Diffusion when you first generate an image.
4. Keep the helper window open. It opens Manga Studio with a temporary connection key.

The default model is Stable Diffusion 1.5 for compatibility with 4 GB GPUs. Character consistency uses IP-Adapter reference conditioning. Inpainting is downloaded lazily the first time it is used.

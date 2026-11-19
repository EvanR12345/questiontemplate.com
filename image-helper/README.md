# QuestionTemplate Image Helper

The Audio, Comic & panel tools and Story → video pages use the shared QuestionTemplate NVIDIA helper. Start the existing installation with its `start-windows.bat`, open the pairing link, and choose **Comic & panel tools** in Studio. The shared helper keeps its installed Python environment, model files and image queue. [Shared helper instructions](../nvidia-helper/README.md)

This directory preserves the earlier standalone image server. Its launcher creates a separate Python environment and installs Torch. It implements the earlier image API. The current Comic workspace connects through the shared helper's image queue.

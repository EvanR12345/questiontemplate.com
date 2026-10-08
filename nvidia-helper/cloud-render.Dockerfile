FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir Pillow==11.3.0 'google-cloud-storage>=3.4,<4'
WORKDIR /opt/studio
# Explicit source allowlist: no models, local media, project exports or secrets.
COPY studio_cloud_render.py studio_render.py studio_data.py studio_storage.py \
     google_storage.py studio_qc.py studio_branding.py studio_engagement.py \
     render_queue.py ./
RUN useradd --uid 10001 --create-home studio && mkdir /work && chown studio:studio /work
USER studio
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STUDIO_FFMPEG=/usr/bin/ffmpeg STUDIO_RENDER_WORKERS=1
CMD ["python", "studio_cloud_render.py"]

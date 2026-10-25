# AquaScan - production image
FROM python:3.11-slim

# System deps needed by opencv/ultralytics/pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# CPU-only torch keeps the image much smaller than the default CUDA build.
# ultralytics will pull in torch anyway if not already present, so we
# install the CPU wheel first from PyTorch's own index.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p dashboard/uploads

ENV PORT=5000
EXPOSE 5000

# 1 worker: the CV model is loaded per-process, and most student-project
# traffic is low; bump --workers if you have RAM (each worker loads the
# model separately) and paid hosting with more memory.
CMD gunicorn --chdir dashboard --bind 0.0.0.0:${PORT} --workers 1 --threads 4 --timeout 120 app:app

FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies: ffmpeg for audio extraction
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# PyTorch CPU-only from the official CPU index to avoid pulling CUDA libs
COPY requirements.txt .
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

COPY app.py .

# Pre-download whisper model at build time (avoids download on first request).
# Uses the smallest model by default; override WHISPER_MODEL_SIZE at runtime for larger.
ARG WHISPER_MODEL_SIZE=base
RUN python -c "import whisperx; whisperx.load_model('${WHISPER_MODEL_SIZE}', device='cpu', compute_type='int8')"

EXPOSE 8000

# Run as non-root
RUN useradd --create-home appuser
USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

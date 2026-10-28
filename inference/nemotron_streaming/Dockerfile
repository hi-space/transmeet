FROM python:3.11-slim

LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming="true"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TORCHDYNAMO_DISABLE=1 \
    TORCHINDUCTOR_DISABLE=1 \
    OMP_NUM_THREADS=2 \
    MODEL_DIR=/opt/ml/model

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn[standard]==0.32.1"

WORKDIR /opt/streaming
COPY inference/__init__.py /opt/streaming/inference/__init__.py
COPY inference/nemotron_streaming /opt/streaming/inference/nemotron_streaming

EXPOSE 8080
ENTRYPOINT ["python", "-m", "inference.nemotron_streaming.bootstrap"]

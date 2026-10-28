# Exact AWS DLC currently serving this JumpStart checkpoint: Torch 2.1/CUDA 11.8.
FROM 763104351884.dkr.ecr.us-east-1.amazonaws.com/huggingface-pytorch-inference@sha256:c1fa2c7f57e0e9637267afbc199e2ae51d152d997278d28292723b16f118e4b1 AS dependencies

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=2 \
    MODEL_DIR=/opt/ml/model

COPY inference/whisper_streaming/requirements.txt /opt/whisper-requirements.txt
RUN pip install --no-cache-dir -r /opt/whisper-requirements.txt \
    && python -c "import torch, transformers, fastapi, uvicorn, silero_vad, onnxruntime; assert torch.__version__.startswith('2.1.'); assert transformers.__version__ == '4.39.3'; assert 'CPUExecutionProvider' in onnxruntime.get_available_providers()" \
    && command -v ffmpeg \
    && pip freeze > /opt/whisper-installed-packages.txt

FROM dependencies
LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming="true"

WORKDIR /opt/streaming
COPY inference/__init__.py /opt/streaming/inference/__init__.py
COPY inference/whisper_streaming /opt/streaming/inference/whisper_streaming

EXPOSE 8080
ENTRYPOINT ["python", "-m", "inference.whisper_streaming.bootstrap"]

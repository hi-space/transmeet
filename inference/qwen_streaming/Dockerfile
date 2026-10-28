# Official vLLM 0.14.0 linux/amd64 image, Torch 2.9.1 / CUDA 12.9.
FROM vllm/vllm-openai@sha256:48a03c91eaa04fb7e71c77121a85e661d62de1b2207edf591aea6a05d779f9ef

LABEL com.amazonaws.sagemaker.capabilities.bidirectional-streaming="true"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1 \
    VLLM_WORKER_MULTIPROC_METHOD=spawn \
    OMP_NUM_THREADS=2 \
    MODEL_DIR=/opt/ml/model

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 libgomp1 libcairo2-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY inference/qwen_streaming/requirements.txt /opt/qwen-requirements.txt
RUN pip install --no-cache-dir --ignore-installed --no-deps blinker==1.9.0 \
    && pip install --no-cache-dir -r /opt/qwen-requirements.txt \
    && pip check \
    && pip freeze > /opt/qwen-installed-packages.txt

WORKDIR /opt/streaming
COPY inference/__init__.py /opt/streaming/inference/__init__.py
COPY inference/qwen_streaming /opt/streaming/inference/qwen_streaming

EXPOSE 8080
ENTRYPOINT ["python3", "-m", "inference.qwen_streaming.bootstrap"]

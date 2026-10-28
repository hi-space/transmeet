FROM python:3.13-slim

# 녹음 구간(PCM)을 MP3 로 합친다 (app/audio_store.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Transcribe and SageMaker HTTP/2 require incompatible awscrt versions.
# Keep their native libraries in separate processes and Python environments.
RUN python -m venv /opt/nemotron-stream \
    && /opt/nemotron-stream/bin/pip install --no-cache-dir \
       "aws-sdk-sagemaker-runtime-http2[awscrt]==0.12.0" "boto3==1.43.106"
ENV NEMOTRON_STREAM_PYTHON=/opt/nemotron-stream/bin/python

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

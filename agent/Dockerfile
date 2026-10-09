# AgentCore Runtime image (linux/arm64). Serves POST /invocations + GET /ping on :8080.
FROM --platform=linux/arm64 python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SKILLS_DIR=/app/skills \
    WORK_BASE=/tmp/transmeet-work

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runtime/ ./runtime/
COPY skills/ ./skills/

EXPOSE 8080
CMD ["python", "-m", "runtime.app"]

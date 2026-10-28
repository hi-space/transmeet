"""SageMaker passes `serve`; all dependencies are installed in the image."""
import os
import sys


def main():
    os.execvp(sys.executable, [
        sys.executable, "-m", "uvicorn", "inference.whisper_streaming.server:app",
        "--host", "0.0.0.0", "--port", "8080", "--workers", "1",
        "--ws-max-size", "65536", "--ws-max-queue", "32",
    ])


if __name__ == "__main__":
    main()

"""Install the exact offline wheels bundled with the pinned JumpStart artifact."""
import os
import subprocess
import sys


def main():
    requirements = os.path.join(os.environ.get("MODEL_DIR", "/opt/ml/model"), "code", "requirements.txt")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
         "-r", requirements],
        check=True,
    )
    os.execvp(sys.executable, [
        sys.executable, "-m", "uvicorn", "inference.nemotron_streaming.server:app",
        "--host", "0.0.0.0", "--port", "8080", "--workers", "1",
        "--ws-max-size", "65536", "--ws-max-queue", "32",
    ])


if __name__ == "__main__":
    main()

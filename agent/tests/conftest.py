import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
sys.path.insert(0, str(SKILLS / "meeting-minutes-page" / "scripts"))
sys.path.insert(0, str(SKILLS / "meeting-diagrams" / "scripts"))

# 백엔드 검증기와 공용 코퍼스(backend/tests/validator_corpus.py)로 두 검증기의 동작이 같은지 확인한다.
BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND / "tests"))
sys.path.insert(0, str(BACKEND))

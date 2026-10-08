import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"
sys.path.insert(0, str(SKILLS / "meeting-minutes-page" / "scripts"))
sys.path.insert(0, str(SKILLS / "meeting-diagrams" / "scripts"))

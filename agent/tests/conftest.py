import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
SKILLS = AGENT / "skills"
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(SKILLS / "meeting-minutes-page" / "scripts"))
sys.path.insert(0, str(SKILLS / "meeting-diagrams" / "scripts"))
sys.path.insert(0, str(AGENT / "tests"))

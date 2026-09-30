import sys
from pathlib import Path

# Os módulos do projeto são importados pela raiz (from config import ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

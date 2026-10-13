# tests/eval_gate/conftest.py
from pathlib import Path
import importlib.util
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def load_eval_gate():
    spec = importlib.util.spec_from_file_location("eval_gate", REPO / "scripts" / "eval_gate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_gate"] = mod
    spec.loader.exec_module(mod)
    return mod

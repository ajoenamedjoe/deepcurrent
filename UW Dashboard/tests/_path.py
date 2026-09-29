import os
import sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def sample(name):
    with open(os.path.join(SAMPLES, name), "r", encoding="utf-8") as fh:
        return fh.read()

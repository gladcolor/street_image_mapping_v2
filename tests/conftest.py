"""Test set-up: SIM's gsv_pano modules write log files into the current folder when
imported (utils.py -> info.log, pano.py -> its log config), so the tests run from a
temporary folder and never touch the logs kept in the repository."""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "gsv_pano"))
os.chdir(tempfile.mkdtemp(prefix="sim_tests_"))

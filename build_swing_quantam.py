"""Rebuild the swing_quantam board. jobs.py runs scripts, not `-m`, hence the shim."""
import sys
from swing_quantam.__main__ import main
sys.exit(main(sys.argv[1:]))

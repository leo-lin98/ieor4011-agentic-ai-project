"""College football (NCAA FBS/FCS) candidate pairs. How the filtering works: FILTERING.md.

    python -m candidates sports football/cfb
"""
from candidates.sports.football.run import _DEFAULT, run_league

from . import config


def run(write=True, frames=None, approvals=None, window_days=_DEFAULT, now=None):
    return run_league(config, write=write, frames=frames, approvals=approvals, window_days=window_days, now=now)

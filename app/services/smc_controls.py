"""Explicit direction override; never changes strategy safety thresholds."""
from .. import db

def direction():
    value = db.get_setting("smc_direction", "AUTO")
    return value if value in ("AUTO", "LONG", "SHORT") else "AUTO"

def allows(row, mode=None):
    mode = direction() if mode is None else mode
    return mode == "AUTO" or row.get("side") == mode

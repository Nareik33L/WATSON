"""Helper protocol, spool, exclusions, and platform abstraction."""

from watson.capture.sidecar import SidecarError, load_sidecar, sidecar_schema_path
from watson.capture.spool import SpoolError, SpoolPair, iter_pairs

__all__ = [
    "SidecarError",
    "SpoolError",
    "SpoolPair",
    "iter_pairs",
    "load_sidecar",
    "sidecar_schema_path",
]

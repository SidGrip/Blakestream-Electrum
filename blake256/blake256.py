"""
Blake-256 (8-round sphlib) hash function for Blakecoin.

This wraps the C extension built from the same sphlib code used in the
Blakecoin daemon, guaranteeing identical hash output.

Supports two backends:
  1. _blake256 C extension (Linux AppImage, native)
  2. blake256.dll via ctypes (Windows)

Usage:
    from blake256 import blake256_hash
    digest = blake256_hash(data)  # returns 32 bytes
"""

import os
import sys
import ctypes

_hash_func = None

# Try native C extension first
try:
    from _blake256 import hash as _hash_func
except ImportError:
    pass

# Fallback: load blake256.dll via ctypes (Windows builds)
if _hash_func is None:
    _dll = None
    for search_dir in [os.path.dirname(__file__), os.path.dirname(sys.executable), '.']:
        dll_path = os.path.join(search_dir, 'blake256.dll')
        if os.path.exists(dll_path):
            try:
                _dll = ctypes.CDLL(dll_path)
                break
            except OSError:
                continue
    # Also check c:\tmp (Wine build location)
    if _dll is None and sys.platform == 'win32':
        for p in ['c:\\tmp\\blake256.dll', 'blake256.dll']:
            try:
                _dll = ctypes.CDLL(p)
                break
            except OSError:
                pass
    if _dll is not None:
        _dll.blake256_hash.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p]
        _dll.blake256_hash.restype = None
        def _ctypes_hash(data):
            out = ctypes.create_string_buffer(32)
            _dll.blake256_hash(data, len(data), out)
            return out.raw
        _hash_func = _ctypes_hash

if _hash_func is None:
    raise ImportError("Blake-256: neither _blake256 C extension nor blake256.dll found")


def blake256_hash(data: bytes) -> bytes:
    """Compute Blake-256 (8-round) hash. Returns 32-byte digest."""
    return _hash_func(data)


# Older ElectrumX/coin helper paths import a legacy blake_hash symbol.
# Keep that alias available so both the wallet-side and server-side Blake
# hashing code can share this module without import-shape drift.
blake_hash = blake256_hash

#!/usr/bin/env python3
"""Test vectors for Blake-256 (8-round sphlib) hash used by Blakecoin."""

import struct
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import _blake256


def test_genesis_block():
    """Verify Blake-256 hash of the Blakecoin genesis block header."""
    nVersion = 112
    hashPrevBlock = b'\x00' * 32
    hashMerkleRoot = bytes.fromhex(
        '9e4654d5bb91c723c3dbbaee57761d06ed10ac17f4d8841746aeec7ff8206ddc'
    )[::-1]
    nTime = 1381036817
    nBits = 503382015  # 0x1e00ffff
    nNonce = 127057407

    header = struct.pack('<i', nVersion)
    header += hashPrevBlock
    header += hashMerkleRoot
    header += struct.pack('<I', nTime)
    header += struct.pack('<I', nBits)
    header += struct.pack('<I', nNonce)

    assert len(header) == 80, f"Header should be 80 bytes, got {len(header)}"

    hash_bytes = _blake256.hash(header)
    hash_display = hash_bytes[::-1].hex()

    expected = '000000ba5cae4648b1a2b823f84cc3424e5d96d7234b39c6bb42800b2c7639be'
    assert hash_display == expected, f"Genesis hash mismatch: {hash_display} != {expected}"
    print("PASS: Genesis block hash matches")


def test_empty_input():
    """Verify Blake-256 hash of empty input."""
    result = _blake256.hash(b'')
    assert len(result) == 32, f"Hash should be 32 bytes, got {len(result)}"
    # Known Blake-256 (8-round) hash of empty input
    print(f"PASS: Empty input hash = {result.hex()}")


def test_output_length():
    """Verify all outputs are 32 bytes."""
    for data in [b'', b'a', b'hello', b'\x00' * 80, b'\xff' * 256]:
        result = _blake256.hash(data)
        assert len(result) == 32, f"Hash of {len(data)}-byte input should be 32 bytes"
    print("PASS: All output lengths are 32 bytes")


if __name__ == '__main__':
    test_genesis_block()
    test_empty_input()
    test_output_length()
    print("\nAll Blake-256 tests passed!")

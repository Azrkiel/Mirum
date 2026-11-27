"""The pinned mirum-native kernel binary (N1c-2).

These values are code, not configuration: nothing in the environment can change what the adapter accepts.
The same SHA-256 is committed in ``native-kernel/mirum-native.sha256`` (sha256sum format) for the image
build, and a test keeps the two equal. A new kernel is a reviewed change to this file.

The values come from mirum-kernel commit 3501017 (CI run 45939498059, artifact ``mirum-native-musl``) and the
public image published from it.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ['NativePin', 'PIN', 'IMAGE', 'BINARY_PATH', 'BINARY_SHA256', 'KERNEL_COMMIT', 'KERNEL_VERSION',
           'PROTOCOL', 'MAX_BINARY_BYTES']

IMAGE = 'ghcr.io/azrkiel/mirum-native@sha256:98e503588daab23582eb6ca531b69b341e8422a3072c86524f398cbfe4afe09d'
BINARY_PATH = '/opt/mirum-native/bin/mirum-native'
BINARY_SHA256 = 'b9d4f28d26049485fb3cbe7a7996c5f550180716e8951de7c05742a839f1e9bd'
KERNEL_COMMIT = '3501017b40c07b77d9cd48c0e7c1dcee744fd5fb'
KERNEL_VERSION = '0.1.0'
PROTOCOL = 'mirum_native_plate_v1'
MAX_BINARY_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class NativePin:
    sha256: str
    commit: str
    version: str
    protocol: str = PROTOCOL
    name: str = 'mirum-native'


PIN = NativePin(sha256=BINARY_SHA256, commit=KERNEL_COMMIT, version=KERNEL_VERSION)

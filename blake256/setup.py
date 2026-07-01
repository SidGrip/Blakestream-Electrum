import sys
import os
from setuptools import setup, Extension

# On Windows (Wine builds), skip C extension — use blake256.dll via ctypes instead
ext_modules = []
if sys.platform != 'win32' and os.environ.get('SKIP_BLAKE256_EXT') != '1':
    blake256_ext = Extension(
        '_blake256',
        sources=['blake256_wrapper.c', 'blake.c'],
        include_dirs=['.'],
        define_macros=[('PY_SSIZE_T_CLEAN', None)],
    )
    ext_modules = [blake256_ext]

setup(
    name='blake256',
    version='1.0.0',
    description='Blake-256 (8-round sphlib) hash for Blakecoin',
    ext_modules=ext_modules,
    py_modules=['blake256'],
)

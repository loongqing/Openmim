"""Wrapper to run pytest under environments where mkdir(mode=0o700) produces
inaccessible directories (Windows + sandboxed runtime). Strips the mode from
os.mkdir so pytest's tmp machinery creates plain directories.

Usage: python run_pytest.py [pytest args...]
"""
import os
import sys

_orig_mkdir = os.mkdir


def _mkdir(path, mode=0o777, *, dir_fd=None):
    if dir_fd is not None:
        return _orig_mkdir(path, dir_fd=dir_fd)
    return _orig_mkdir(path)


os.mkdir = _mkdir

import pytest  # noqa: E402

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:]))

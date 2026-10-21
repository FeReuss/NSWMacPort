#!/usr/bin/env python3
"""Prepare shaders from local NSW files; never start the game."""
import sys
from build_app import BuildError, main

if __name__ == '__main__':
    try:
        main([*sys.argv[1:], '--shaders-only'])
    except (BuildError, OSError, ValueError) as error:
        sys.exit(str(error))

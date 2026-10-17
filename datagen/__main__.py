"""Expose the synthetic data generator as ``python -m datagen``.

The module delegates argument parsing and JSON writing to
``datagen.builder.main``. It relates to ``datagen/builder.py``,
``ops/seed/README.md``, and the manual ``.github/workflows/seed.yml`` workflow.
Its purpose is to provide reproducible data for local development and pipeline
review without querying Bright Data.
"""

from datagen.builder import main

if __name__ == "__main__":
    raise SystemExit(main())

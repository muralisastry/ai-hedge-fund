"""Pluggable data providers behind src/tools/api.py.

The public functions in ``src/tools/api.py`` keep their exact signatures and
return the same ``src.data.models`` Pydantic types. They delegate to
``router``, which picks a provider per data type from configuration
(``DATA_PROVIDER`` / ``DATA_PROVIDER_<TYPE>``) and optionally falls back to
Financial Datasets on miss/error.

Phase 0 ships only the Financial Datasets provider with everything defaulting
to ``fd`` — i.e. zero behavior change. Later phases add ``internal``,
``polygon`` and ``sec`` providers and flip per-type defaults.
"""

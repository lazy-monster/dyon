"""FastAPI routers for the visualization module.

Each submodule builds one router via a ``build_*_router(...)`` factory, mirroring
the convention in :mod:`dyon.services.api`. ``serve.mount_visualization`` wires
them onto a host app under the ``/api/viz`` prefix.
"""

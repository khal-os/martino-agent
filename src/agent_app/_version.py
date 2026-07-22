"""Single source of truth for the agent version.

Everything reads from here: the package (`agent_app.__version__`), `pyproject.toml`
(dynamic version via hatch), `/health`, and the observability Resource
(`service.version`). Bump this one line to release.

Kept in its own import-free module so `config.py` can read it without triggering
the package's import chain (config → agents → config).
"""

__version__ = "0.1.0"

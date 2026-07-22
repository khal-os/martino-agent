"""Version is unified from a single source (_version.py) and surfaced everywhere."""


def test_single_source_of_truth():
    from agent_app import __version__
    from agent_app._version import __version__ as raw

    assert __version__ == raw
    assert __version__.count(".") >= 2  # semantic-ish


def test_config_agent_version_matches():
    from agent_app import __version__
    from agent_app.config import get_settings

    assert get_settings().agent_version == __version__


def test_pyproject_version_is_dynamic():
    """pyproject reads the version from _version.py (installed metadata matches)."""
    from importlib.metadata import version

    from agent_app import __version__

    assert version("nsmtx-agent-template") == __version__


def test_health_reports_version():
    from agent_app import __version__
    from agent_app.main import health

    body = health().body.decode()
    assert __version__ in body
    assert '"version"' in body

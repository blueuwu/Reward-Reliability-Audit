"""Immutable grader plugin loaded only by the hardened/oracle runner.

The plugin verifies fail-closed that the active pytest configuration and
rootdir are the immutable grader assets under ``/opt/grader`` or
``/opt/oracle``. It is loaded explicitly with ``-p grader_plugin`` while
``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`` prevents any other plugin from loading.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    rootdir = str(config.rootdir)
    inifile = config.inifile
    ini = str(inifile) if inifile is not None else ""
    if not (rootdir.startswith("/opt/grader") or rootdir.startswith("/opt/oracle")):
        raise pytest.UsageError("grader rootdir must be an immutable grader asset path")
    if not (ini.startswith("/opt/grader") or ini.startswith("/opt/oracle")):
        raise pytest.UsageError("grader config must be an immutable grader asset path")

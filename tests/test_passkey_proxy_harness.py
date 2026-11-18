from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_passkey_proxy_relay_lifecycle(tmp_path: Path) -> None:
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("A host C++ compiler is required for the relay lifecycle test")

    repository = Path(__file__).resolve().parents[1]
    if repository.name == "mutants":
        pytest.skip("The mutation workspace tests Python code only")
    relay = repository / "components" / "dimplex_passkey_proxy"
    stubs = repository / "tests" / "relay_stubs"
    binary = tmp_path / "passkey-proxy-relay-test"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-I",
            str(stubs),
            "-I",
            str(relay),
            str(relay / "dimplex_passkey_proxy.cpp"),
            str(repository / "tests" / "test_passkey_proxy_relay.cpp"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    subprocess.run([str(binary)], check=True, timeout=10)

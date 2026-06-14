"""Provision and remove the speech venv for offline dictation."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Callable

logger = logging.getLogger(__name__)


def venv_valid() -> bool:
    from core.speech_paths import SPEECH_PYTHON, SPEECH_PYVENV_CFG

    return SPEECH_PYTHON.is_file() and SPEECH_PYVENV_CFG.is_file()


def provision(
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
) -> None:
    from core.speech_paths import REQUIRED_PACKAGES, SPEECH_PYTHON, SPEECH_VENV

    SPEECH_VENV.mkdir(parents=True, exist_ok=True)

    if not SPEECH_PYTHON.is_file():
        logger.info("Creating speech venv at %s", SPEECH_VENV)
        _run(
            [sys.executable, "-m", "venv", "--system-site-packages", str(SPEECH_VENV)],
            on_stdout,
            on_stderr,
        )

    logger.info("Installing packages into speech venv: %s", REQUIRED_PACKAGES)
    _run(
        [str(SPEECH_PYTHON), "-m", "pip", "install", "--quiet"] + REQUIRED_PACKAGES,
        on_stdout,
        on_stderr,
    )


def remove() -> None:
    import shutil

    from core.speech_paths import SPEECH_VENV

    if SPEECH_VENV.exists():
        logger.info("Removing speech venv at %s", SPEECH_VENV)
        shutil.rmtree(SPEECH_VENV)


def _run(
    cmd: list[str],
    on_stdout: Callable[[str], None] | None,
    on_stderr: Callable[[str], None] | None,
) -> None:
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        for line in proc.stdout:
            line = line.rstrip("\n")
            logger.debug("venv: %s", line)
            if on_stdout:
                on_stdout(line)
        for line in proc.stderr:
            line = line.rstrip("\n")
            logger.warning("venv: %s", line)
            if on_stderr:
                on_stderr(line)
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"Command {' '.join(cmd)} failed with exit code {ret}")

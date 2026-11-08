"""Run untrusted Python in a child process with hard limits (SPEC 12.4 sandbox rules).

The container provides isolation (no egress, read-only root, no capabilities, pid/CPU/memory
caps); this process layer adds a scrubbed environment, a tmpfs working dir per call, rlimits,
a wall-clock kill of the whole process group, and output caps.
"""

import asyncio
import contextlib
import os
import resource
import signal
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_STREAM_BYTES = 256 * 1024  # stdout/stderr returned to the agent
MAX_FILE_BYTES = 50 * 1024 * 1024  # output cap (SPEC 12.4)
MEMORY_BYTES = 1024 * 1024 * 1024
OPEN_FILES = 256
# No RLIMIT_NPROC: it counts every process of the uid, so it misfires outside the container.
# The container's pids_limit caps fork bombs instead (SPEC 21.2).


DRAIN_SECONDS = 5


def safe_name(name: str) -> str:
    """A plain file name: no directories, and never `.`, `..` or empty."""
    base = Path(name).name
    return base if base not in ("", ".", "..") else "input"


def unique_names(names: list[str]) -> list[str]:
    """Same order; a repeated name gets `-2`, `-3` before its suffix (a.csv, a-2.csv)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name, n = safe_name(raw), 1
        stem, dot, ext = name.rpartition(".") if "." in name[1:] else (name, "", "")
        while name in seen:
            n += 1
            name = f"{stem}-{n}{dot}{ext}"
        seen.add(name)
        out.append(name)
    return out


@dataclass(frozen=True)
class Execution:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    outputs: dict[str, bytes]  # files the code wrote under ./out


def _limits(cpu_seconds: int) -> Callable[[], None]:
    def apply() -> None:  # runs in the child between fork and exec
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES, OPEN_FILES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


async def run_python(
    code: str, inputs: dict[str, bytes], timeout_s: int, workroot: Path
) -> Execution:
    """Execute `code` with `inputs` available read-only under ./in; collect files from ./out."""
    with tempfile.TemporaryDirectory(dir=workroot) as tmp:
        work = Path(tmp)
        (work / "in").mkdir()
        (work / "out").mkdir()
        for name, body in inputs.items():
            path = work / "in" / safe_name(name)  # no directory traversal via names
            path.write_bytes(body)
            path.chmod(0o444)
        (work / "main.py").write_text(code)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(work),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(work / ".mpl"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            # One CPU in the container: per-core BLAS/OpenMP pools would also blow the memory cap.
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "main.py",
            cwd=work,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_limits(timeout_s),
            start_new_session=True,  # its own process group, so a timeout kills everything
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            # A child that left the group (setsid) can hold the pipes open: drain briefly only.
            # ponytail: the escaped child lives until the container's pids/memory caps or restart.
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), DRAIN_SECONDS)
            except TimeoutError:
                stdout, stderr = b"", b"killed: a child process escaped the time limit"
        outputs: dict[str, bytes] = {}
        total = 0
        for path in sorted((work / "out").rglob("*")):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
                if total > MAX_FILE_BYTES:
                    break
                outputs[str(path.relative_to(work / "out"))] = path.read_bytes()
        return Execution(
            stdout=stdout[:MAX_STREAM_BYTES].decode(errors="replace"),
            stderr=stderr[:MAX_STREAM_BYTES].decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timed_out=timed_out,
            outputs=outputs,
        )

from __future__ import annotations

import io
import os
import select
import sys
import threading
import time

CAPTURE_THREAD_JOIN_TIMEOUT_SECONDS = 10.0


class _TeeStream(io.TextIOBase):
    """
    Windows-only: wraps an existing text stream to mirror writes to it
    while collecting the text for ConsoleCapture.
    """

    def __init__(self, original: io.TextIOBase, chunks: list[str]) -> None:
        self._original = original
        self._chunks = chunks

    def write(self, s: str) -> int:
        self._chunks.append(s)
        return self._original.write(s)

    def flush(self) -> None:
        self._original.flush()

    def fileno(self) -> int:
        return self._original.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return getattr(self._original, "errors", "replace")


class ConsoleCapture:
    """
    Mirror stdout/stderr to the console while collecting the full text.

    On Linux/macOS this captures Python prints and subprocess output that
    inherits the current process file descriptors (fd-level redirect via pipe).

    On Windows, CPython's stdout may use WriteConsoleW directly, bypassing
    the fd table after os.dup2(). Therefore on Windows we use a Python-level
    stream replacement (_TeeStream) that mirrors and collects text without
    any fd manipulation. Subprocess output is not captured on Windows, but
    Python-level print() is fully captured and mirrored.
    """

    def __init__(self) -> None:
        self._stdout_copy: int | None = None
        self._stderr_copy: int | None = None
        self._stdout_pipe: tuple[int, int] | None = None
        self._stderr_pipe: tuple[int, int] | None = None
        self._threads: list[threading.Thread] = []
        self._chunks: list[str] = []
        self._stop_event = threading.Event()
        # Windows-only: original streams saved for restore
        self._win_orig_stdout: io.TextIOBase | None = None
        self._win_orig_stderr: io.TextIOBase | None = None

    # ------------------------------------------------------------------
    # Windows path: Python-level stream tee (no fd manipulation)
    # ------------------------------------------------------------------

    def _enter_windows(self) -> None:
        self._win_orig_stdout = sys.stdout  # type: ignore[assignment]
        self._win_orig_stderr = sys.stderr  # type: ignore[assignment]
        sys.stdout = _TeeStream(self._win_orig_stdout, self._chunks)  # type: ignore[assignment]
        sys.stderr = _TeeStream(self._win_orig_stderr, self._chunks)  # type: ignore[assignment]

    def _exit_windows(self) -> None:
        sys.stdout = self._win_orig_stdout  # type: ignore[assignment]
        sys.stderr = self._win_orig_stderr  # type: ignore[assignment]
        self._win_orig_stdout = None
        self._win_orig_stderr = None

    # ------------------------------------------------------------------
    # Non-Windows path: fd-level pipe redirect with select-based pump
    # ------------------------------------------------------------------

    def _pump(self, read_fd: int, mirror_fd: int) -> None:
        try:
            while True:
                readable, _, _ = select.select([read_fd], [], [], 0.1)
                if not readable:
                    if self._stop_event.is_set():
                        break
                    continue
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break
                self._chunks.append(chunk.decode("utf-8", errors="replace"))
                try:
                    os.write(mirror_fd, chunk)
                except OSError:
                    pass
        except OSError:
            pass
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

    def _debug_write(self, message: str) -> None:
        line = f"{message}\n"
        self._chunks.append(line)
        target_fd = self._stdout_copy
        try:
            os.write(target_fd if target_fd is not None else sys.__stdout__.fileno(), line.encode("utf-8"))
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def __enter__(self) -> "ConsoleCapture":
        if sys.platform == "win32":
            self._enter_windows()
            return self

        sys.stdout.flush()
        sys.stderr.flush()

        self._stdout_copy = os.dup(sys.stdout.fileno())
        self._stderr_copy = os.dup(sys.stderr.fileno())
        self._stdout_pipe = os.pipe()
        self._stderr_pipe = os.pipe()

        stdout_thread = threading.Thread(
            target=self._pump,
            args=(self._stdout_pipe[0], self._stdout_copy),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._pump,
            args=(self._stderr_pipe[0], self._stderr_copy),
            daemon=True,
        )
        self._threads = [stdout_thread, stderr_thread]
        for thread in self._threads:
            thread.start()

        os.dup2(self._stdout_pipe[1], sys.stdout.fileno())
        os.dup2(self._stderr_pipe[1], sys.stderr.fileno())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if sys.platform == "win32":
            self._exit_windows()
            return

        exit_start = time.perf_counter()
        print("[console-capture] exit begin", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()

        if self._stdout_copy is None or self._stderr_copy is None:
            return
        if self._stdout_pipe is None or self._stderr_pipe is None:
            return

        self._debug_write("[console-capture] exit begin")
        os.close(self._stdout_pipe[1])
        os.close(self._stderr_pipe[1])
        self._debug_write("[console-capture] closed capture pipe writer fds")
        os.dup2(self._stdout_copy, sys.stdout.fileno())
        os.dup2(self._stderr_copy, sys.stderr.fileno())
        self._stop_event.set()
        self._debug_write("[console-capture] restored stdout/stderr fds")

        deadline = exit_start + CAPTURE_THREAD_JOIN_TIMEOUT_SECONDS
        while True:
            alive_threads = [thread.name for thread in self._threads if thread.is_alive()]
            if not alive_threads:
                break
            elapsed = time.perf_counter() - exit_start
            remaining = deadline - time.perf_counter()
            self._debug_write(
                "[console-capture] waiting for pump threads " f"elapsed={elapsed:.2f}s alive={alive_threads}"
            )
            if remaining <= 0:
                self._debug_write(
                    "[console-capture] pump thread join timeout; continuing with daemon pump threads still alive"
                )
                break
            for thread in self._threads:
                thread.join(timeout=min(1.0, max(0.0, remaining)))

        self._debug_write(f"[console-capture] exit finished elapsed={time.perf_counter() - exit_start:.2f}s")
        os.close(self._stdout_copy)
        os.close(self._stderr_copy)
        print(f"[console-capture] exit finished elapsed={time.perf_counter() - exit_start:.2f}s", flush=True)

    @property
    def text(self) -> str:
        return "".join(self._chunks)

"""
Esc-to-interrupt while a turn is running (Claude Code's "esc to interrupt").

While active, a daemon thread puts the terminal in cbreak mode and watches
stdin; a lone Esc key (not the start of an arrow-key sequence) sends this
process SIGINT -- i.e. KeyboardInterrupt in the main thread, which the REPL already treats as
"stop this turn". paused() hands the terminal back for y/N prompts.
"""
import os
import signal
import select
import sys
import threading
from contextlib import contextmanager

ESC = b"\x1b"
SEQUENCE_WINDOW = 0.05  # arrow keys etc. send ESC followed immediately by more bytes


class EscWatcher:
    def __init__(self):
        self.enabled = sys.stdin.isatty()
        self._thread = None
        self._stop = threading.Event()
        self._saved_attrs = None

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        import termios
        import tty

        fd = sys.stdin.fileno()
        self._saved_attrs = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, args=(fd,), daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread is None:
            return
        import termios

        self._stop.set()
        self._thread.join(timeout=0.5)
        self._thread = None
        if self._saved_attrs is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved_attrs)
            self._saved_attrs = None

    @contextmanager
    def paused(self):
        was_running = self._thread is not None
        self.stop()
        try:
            yield
        finally:
            if was_running:
                self.start()

    def _watch(self, fd):
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready or self._stop.is_set():
                continue
            data = os.read(fd, 1)
            if data != ESC:
                continue  # ignore typing while a turn runs
            more, _, _ = select.select([fd], [], [], SEQUENCE_WINDOW)
            if more:
                os.read(fd, 16)  # swallow the rest of an escape sequence (arrow keys)
                continue
            self._stop.set()
            # A real SIGINT (not _thread.interrupt_main) so a main thread blocked
            # in a network read is woken immediately, exactly like ctrl+c.
            os.kill(os.getpid(), signal.SIGINT)
            return

"""Device access on a background thread.

All HID traffic is funnelled through one worker so the UI never blocks and
two requests can't interleave on the same file descriptor.  Callers submit a
function of the open :class:`Controller` and get the result back by signal.
"""

from __future__ import annotations

import queue
import time
import traceback
from typing import Callable

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from .. import commands as C
from ..device import Controller, DeviceError
from ..input_monitor import InputMonitor


class DeviceWorker(QObject):
    connected = pyqtSignal(object)        # DeviceInfo
    disconnected = pyqtSignal(str)        # reason
    result = pyqtSignal(str, object)      # tag, value
    failed = pyqtSignal(str, str)         # tag, message
    input_state = pyqtSignal(object)      # InputState, while streaming
    stream_stopped = pyqtSignal(str)      # reason

    def __init__(self):
        super().__init__()
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._streaming = False
        self.ctl: Controller | None = None
        #: emit no more than this many input frames per second to the UI
        self.stream_fps = 60.0

    # -- called from the UI thread ---------------------------------------

    def submit(self, tag: str, fn: Callable[[Controller], object]) -> None:
        self._queue.put((tag, fn))

    def set_streaming(self, on: bool) -> None:
        """Turn live input reading on or off.

        The worker parks on a blocking queue read when idle, so it has to be
        nudged either way: to notice streaming was switched on, and to leave
        the streaming branch promptly when it is switched off.
        """
        self._streaming = on
        self._queue.put(("__wake__", lambda ctl: None))

    def stop(self) -> None:
        self._running = False
        self._streaming = False
        self._queue.put((None, None))

    # -- thread body ------------------------------------------------------

    def run(self) -> None:
        try:
            self.ctl = Controller().open()
        except DeviceError as exc:
            self.disconnected.emit(str(exc))
            return
        try:
            info = C.read_info(self.ctl)
            info.uid = C.read_uid(self.ctl)
            self.connected.emit(info)
        except DeviceError as exc:
            self.disconnected.emit(str(exc))
            return

        while self._running:
            if self._streaming:
                self._run_stream()
                continue
            tag, fn = self._queue.get()
            if tag is None:
                break
            if tag == "__wake__":
                continue
            self._run_task(tag, fn)

        if self.ctl is not None:
            self.ctl.close()

    def _run_task(self, tag, fn) -> None:
        try:
            self.result.emit(tag, fn(self.ctl))
        except DeviceError as exc:
            self.failed.emit(tag, str(exc))
        except Exception:
            self.failed.emit(tag, traceback.format_exc(limit=3))

    def _run_stream(self) -> None:
        """Read live input until streaming is switched off.

        Queued tasks still run between frames, so the UI stays usable, and
        the monitor's context manager restores the controller's raw-data
        setting however this exits.
        """
        min_gap = 1.0 / max(1.0, self.stream_fps)
        try:
            with InputMonitor(self.ctl) as monitor:
                last = 0.0
                while self._running and self._streaming:
                    while True:
                        try:
                            tag, fn = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if tag is None:
                            self._running = False
                            return
                        if tag != "__wake__":
                            self._run_task(tag, fn)
                    state = monitor.read(timeout=0.05)
                    now = time.monotonic()
                    if state is not None and now - last >= min_gap:
                        last = now
                        self.input_state.emit(state)
        except DeviceError as exc:
            self._streaming = False
            self.stream_stopped.emit(str(exc))
        except Exception:
            self._streaming = False
            self.stream_stopped.emit(traceback.format_exc(limit=2).strip()
                                     .splitlines()[-1])


def start_worker() -> tuple[QThread, DeviceWorker]:
    thread = QThread()
    worker = DeviceWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker

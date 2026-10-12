"""A temporary Windows idle-sleep request owned by the active worker thread.

Never changes a power plan, keeps the display on or prevents deliberate sleep.
Restore the prior thread requirements at the queue boundary, even on failure.
"""
import os

CONTINUOUS = 0x80000000
SYSTEM_REQUIRED = 0x00000001


class ProductionPowerRequest:
    def __init__(self, enabled=True, setter=None, platform=None):
        self.enabled=enabled
        self.setter=setter
        self.platform=os.name if platform is None else platform
        self.previous=None
        self.active=False

    def acquire(self):
        if self.active:return True
        if not self.enabled or self.platform!='nt':return False
        try:
            if self.setter is None:
                import ctypes
                self.setter=ctypes.windll.kernel32.SetThreadExecutionState
            previous=self.setter(CONTINUOUS | SYSTEM_REQUIRED)
            if not previous:return False
            self.previous=previous
            self.active=True
            return True
        except (OSError,AttributeError):return False

    def release(self):
        if self.active:
            try:return bool(self.setter(self.previous | CONTINUOUS))
            except (OSError,AttributeError):return False
            finally:self.active=False
        return True

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self,*args):
        self.release()

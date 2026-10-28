"""Bound only high-frequency frame display writes, never execution checks."""
import re
import threading
import time
from collections import OrderedDict


class FrameProgress:
    def __init__(self, interval=.25, clock=None):
        self.interval=interval
        self.clock=clock or time.monotonic
        self.frames=OrderedDict()
        self.lock=threading.Lock()

    def allow(self, owner, message):
        match=re.fullmatch(r'Rendering frame ([1-9][0-9]*) / ([1-9][0-9]*)',message)
        with self.lock:
            if not match:
                self.frames.pop(owner,None)
                return True
            frame,total=map(int,match.groups())
            previous=self.frames.pop(owner,None)
            now=self.clock()
            emit=(previous is None or frame==1 or frame>=total
                  or total!=previous[1] or frame<=previous[0]
                  or now-previous[2]>=self.interval)
            self.frames[owner]=(frame,total,now if emit else previous[2])
            # Completed executions do not accumulate display state indefinitely.
            while len(self.frames)>256:self.frames.popitem(last=False)
            return emit

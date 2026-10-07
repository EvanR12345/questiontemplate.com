"""Service binding for finish-current remote pause, with no API/model calls."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai_director import OpenAIDirector
from studio_data import new_project
from studio_service import StudioService, JobCancelled


class RemotePauseTest(unittest.TestCase):
    def service(self, root):
        service = StudioService.__new__(StudioService)
        service.cv = threading.Condition()
        service.paused = True
        service.current = None
        service.closed = service.cancel = service.yield_requested = False
        service.config = {}
        service.store = SimpleNamespace(root=Path(root), folder=lambda identity: Path(root)/identity)
        service.director = OpenAIDirector({}, root)
        project = new_project()
        project['settings']['director']['provider'] = 'openai-luna'
        service.select_director(project)
        return service

    def test_collecting_current_response_keeps_future_dispatch_paused(self):
        with tempfile.TemporaryDirectory() as root:
            service = self.service(root)
            with patch.object(service.cv, 'wait', side_effect=lambda *_: setattr(service, 'paused', False)) as wait:
                service.director.receive_gate('Collecting current result')
                self.assertTrue(service.paused)
                wait.assert_not_called()
                service.gate('Preparing next request')
                wait.assert_called_once()

    def test_cancellation_and_shutdown_still_interrupt_current_response(self):
        for flag in ('cancel', 'closed'):
            with tempfile.TemporaryDirectory() as root:
                service = self.service(root)
                setattr(service, flag, True)
                with self.assertRaises(JobCancelled):
                    service.director.receive_gate('Collecting current result')


if __name__ == '__main__': unittest.main()

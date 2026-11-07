import os
import queue
import tempfile
import threading
import unittest

import jxlerate
from jxlerate.core import NullReporter, RunOptions, run_batch
from jxlerate.gui.worker import (
    DoneEvent,
    LogEvent,
    ProgressEvent,
    QueueReporter,
    run_worker,
)
from jxlerate.manifest import write_manifest

try:
    from PIL import Image
except ImportError:
    Image = None


class CorePipelineTests(unittest.TestCase):
    def test_run_options_is_frozen_and_normalizes_output_dir(self):
        options = RunOptions(inputs=("photo.png",), output_dir="relative")
        with self.assertRaises(Exception):
            options.recursive = True

        normalized = options.normalized()
        self.assertEqual(normalized.inputs, ("photo.png",))
        self.assertTrue(os.path.isabs(normalized.output_dir))

    def test_run_batch_reports_no_images(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_batch(RunOptions(inputs=(directory,)), NullReporter())
            self.assertTrue(summary.no_images)
            self.assertEqual(summary.failed, 0)

    def test_cancelled_event_stops_before_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "photo.png"), "wb"):
                pass
            cancel_event = threading.Event()
            cancel_event.set()
            summary = run_batch(
                RunOptions(inputs=(directory,)), NullReporter(), cancel_event)
            self.assertTrue(summary.cancelled)
            self.assertEqual((summary.ok, summary.skipped, summary.failed),
                             (0, 0, 0))

    @unittest.skipIf(Image is None, "Pillow is optional")
    def test_run_batch_writes_outputs_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "photo.jpg")
            exif = Image.Exif()
            exif[0x0112] = 6
            Image.new("RGB", (16, 16), (1, 2, 3)).save(
                source, "JPEG", exif=exif)

            output_dir = os.path.join(directory, "out")
            manifest = os.path.join(directory, "manifest.csv")
            summary = run_batch(
                RunOptions(inputs=(source,), output_dir=output_dir,
                           manifest=manifest),
                NullReporter())

            self.assertEqual((summary.ok, summary.failed), (1, 0))
            self.assertTrue(os.path.isfile(
                os.path.join(output_dir, "photo.jpg")))
            write_manifest(manifest, summary.manifest_rows)
            self.assertEqual(jxlerate.verify_manifest(manifest)[1], 0)


class WorkerTests(unittest.TestCase):
    def test_worker_bridge_does_not_import_tkinter(self):
        import inspect
        from jxlerate.gui import worker

        self.assertNotIn("import tkinter", inspect.getsource(worker))

    def test_queue_reporter_emits_typed_events(self):
        out_queue = queue.Queue()
        reporter = QueueReporter(out_queue)
        reporter.start(3)
        reporter.progress(1, 3, "photo.png")
        reporter.log("hello")
        reporter.error("bad")
        reporter.notice("note")

        events = [out_queue.get_nowait() for _ in range(5)]
        self.assertIsInstance(events[0], LogEvent)
        self.assertIsInstance(events[1], ProgressEvent)
        self.assertEqual((events[1].done, events[1].total), (1, 3))
        self.assertIsInstance(events[2], LogEvent)
        self.assertIsInstance(events[3], LogEvent)
        self.assertTrue(events[3].error)
        self.assertIsInstance(events[4], LogEvent)

    def test_worker_always_emits_done_event(self):
        with tempfile.TemporaryDirectory() as directory:
            out_queue = queue.Queue()
            run_worker(
                RunOptions(inputs=(directory,)), out_queue, threading.Event())

            events = []
            while not out_queue.empty():
                events.append(out_queue.get_nowait())

            self.assertIsInstance(events[-1], DoneEvent)
            self.assertIsNotNone(events[-1].summary)
            self.assertTrue(events[-1].summary.no_images)


if __name__ == "__main__":
    unittest.main()

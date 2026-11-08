import os
import queue
import tempfile
import threading
import time
import unittest
from unittest import mock

import jxlerate
from jxlerate.core import FileResult, NullReporter, RunOptions, run_batch
from jxlerate.fs import collect_file_entries
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


class RecordingReporter(NullReporter):
    def __init__(self):
        self.lines = []
        self.errors = []

    def log(self, text):
        self.lines.append(text)

    def error(self, text):
        self.errors.append(text)


class CorePipelineTests(unittest.TestCase):
    def test_run_options_is_frozen_and_normalizes_output_dir(self):
        options = RunOptions(inputs=("photo.png",), output_dir="relative")
        with self.assertRaises(Exception):
            options.recursive = True

        normalized = options.normalized()
        self.assertEqual(normalized.inputs, ("photo.png",))
        self.assertTrue(os.path.isabs(normalized.output_dir))

    def test_run_options_resolves_jobs(self):
        self.assertEqual(
            RunOptions(inputs=(), jobs=0).normalized().jobs,
            os.cpu_count() or 1)
        self.assertEqual(RunOptions(inputs=(), jobs=-3).normalized().jobs, 1)
        self.assertEqual(RunOptions(inputs=(), jobs=2).normalized().jobs, 2)

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

    def test_parallel_jobs_keep_logs_and_manifest_in_input_order(self):
        with tempfile.TemporaryDirectory() as directory:
            for index in range(6):
                with open(os.path.join(directory, f"{index}.png"), "wb"):
                    pass
            order = [os.path.basename(source) for source, _base in
                     collect_file_entries((directory,), False)]
            output_dir = os.path.join(directory, "out")
            manifest = os.path.join(directory, "manifest.csv")

            def fake_process_one(source, output, options, log_roots=None):
                # Finish in reverse order to force out-of-order completions.
                position = order.index(os.path.basename(source))
                time.sleep(0.02 * (len(order) - position))
                return FileResult(source, output, 10, 4, "test", "written",
                                  saved=6, message=f"done {os.path.basename(source)}")

            reporter = RecordingReporter()
            with mock.patch("jxlerate.core.process_one",
                            side_effect=fake_process_one):
                summary = run_batch(
                    RunOptions(inputs=(directory,), output_dir=output_dir,
                               manifest=manifest, jobs=4),
                    reporter)

            self.assertEqual((summary.ok, summary.failed), (6, 0))
            self.assertEqual(reporter.lines, [f"done {name}" for name in order])
            self.assertEqual(
                [row[0] for row in summary.manifest_rows],
                [os.path.basename(source) for source, _base in
                 collect_file_entries((directory,), False)])

    def test_parallel_jobs_cancel_before_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            for index in range(4):
                with open(os.path.join(directory, f"{index}.png"), "wb"):
                    pass
            cancel_event = threading.Event()
            cancel_event.set()
            with mock.patch("jxlerate.core.process_one") as process:
                summary = run_batch(
                    RunOptions(inputs=(directory,), jobs=4), NullReporter(),
                    cancel_event)
            self.assertTrue(summary.cancelled)
            self.assertEqual((summary.ok, summary.skipped, summary.failed),
                             (0, 0, 0))
            process.assert_not_called()

    def test_skip_existing_leaves_existing_output_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "photo.png")
            with open(source, "wb") as handle:
                handle.write(b"not a real png")
            output_dir = os.path.join(directory, "out")
            os.makedirs(output_dir)
            destination = os.path.join(output_dir, "photo.png")
            with open(destination, "wb") as handle:
                handle.write(b"already done")

            reporter = RecordingReporter()
            summary = run_batch(
                RunOptions(inputs=(source,), output_dir=output_dir,
                           skip_existing=True),
                reporter)

            self.assertEqual((summary.ok, summary.skipped, summary.failed),
                             (0, 1, 0))
            self.assertEqual(
                reporter.lines, [f"SKIP {source}: {destination} already exists"])
            with open(destination, "rb") as handle:
                self.assertEqual(handle.read(), b"already done")

    def test_skip_existing_does_not_skip_in_place_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "photo.png")
            with open(source, "wb") as handle:
                handle.write(b"data")

            with mock.patch(
                    "jxlerate.core.process_one",
                    return_value=FileResult(source, source, 4, 3, "test",
                                            "written")) as process:
                summary = run_batch(
                    RunOptions(inputs=(source,), skip_existing=True),
                    NullReporter())

            process.assert_called_once()
            self.assertEqual((summary.ok, summary.skipped), (1, 0))


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

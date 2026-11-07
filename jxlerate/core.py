# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Shared processing pipeline for the CLI and the GUI.

This module owns the batch orchestration that used to be duplicated in
``jxlerate.py`` and ``jxlerate_gui.py``.  It never prints and never touches
Tk: callers pass a :class:`Reporter` implementation and receive a
:class:`RunSummary`.
"""

from __future__ import annotations

import hashlib
import os
import queue
import shutil
import threading
from dataclasses import dataclass, field, replace
from typing import Protocol

from .formats import optimize_bytes, strip_bytes
from .fs import (
    canonical_path,
    collect_file_entries,
    collect_passthrough_entries,
    copy_passthrough,
    plan_output_path,
    validate_manifest_location,
    validate_output_plan,
    write_file_atomic,
)
from .imaging import lossy_bytes, verify_lossless
from .jxl import to_jxl_file
from .manifest import manifest_row


@dataclass(frozen=True)
class RunOptions:
    """Immutable snapshot of every setting that affects a batch run."""

    inputs: tuple[str, ...]
    recursive: bool = False
    output_dir: str | None = None
    suffix: str = ""
    backup: bool = False
    verify: bool = False
    keep_color: bool = False
    keep_orientation: bool = True
    optimize: bool = False
    max_effort: bool = False
    progressive: bool = False
    lossy_quality: int | None = None
    to_jxl: bool = False
    replace: bool = False
    dry_run: bool = False
    passthrough: bool = False
    skip_clean: bool = False
    only_if_smaller: bool = False
    manifest: str | None = None
    quiet: bool = False
    jobs: int = 1
    skip_existing: bool = False

    def normalized(self) -> "RunOptions":
        """Return a copy with stable input types and an absolute output dir."""
        inputs = tuple(os.fspath(path) for path in self.inputs)
        output_dir = os.path.abspath(self.output_dir) if self.output_dir else None
        jobs = self.jobs
        if jobs == 0:
            jobs = os.cpu_count() or 1
        elif jobs < 0:
            jobs = 1
        if (inputs == self.inputs and output_dir == self.output_dir
                and jobs == self.jobs):
            return self
        return replace(self, inputs=inputs, output_dir=output_dir, jobs=jobs)


@dataclass
class FileResult:
    """Outcome of processing one input file."""

    source: str
    output: str | None
    source_size: int
    output_size: int
    tool: str
    status: str
    digest: str = ""
    saved: int = 0
    message: str = ""


@dataclass
class RunSummary:
    """Aggregate result of a batch run."""

    ok: int = 0
    skipped: int = 0
    failed: int = 0
    saved: int = 0
    cancelled: bool = False
    no_images: bool = False
    error: str | None = None
    output_dir: str | None = None
    manifest_rows: list = field(default_factory=list)


class Reporter(Protocol):
    """Receives progress and log events from :func:`run_batch`."""

    def start(self, total: int) -> None:
        ...

    def progress(self, done: int, total: int, name: str) -> None:
        ...

    def log(self, text: str) -> None:
        ...

    def error(self, text: str) -> None:
        ...

    def notice(self, text: str) -> None:
        ...


class NullReporter:
    """Reporter that discards everything."""

    def start(self, total: int) -> None:
        pass

    def progress(self, done: int, total: int, name: str) -> None:
        pass

    def log(self, text: str) -> None:
        pass

    def error(self, text: str) -> None:
        pass

    def notice(self, text: str) -> None:
        pass


def process_file(source: str, options: RunOptions) -> tuple[bytes, int, bool, str]:
    """Strip, optionally recompress, and return (data, original_size, changed, tool)."""
    with open(source, "rb") as f:
        original = f.read()
    ext = os.path.splitext(source)[1]
    data = strip_bytes(original, ext, options.keep_color,
                       options.keep_orientation)
    tool = "metadata"

    if options.lossy_quality is not None:
        candidate, name = lossy_bytes(
            data, ext, options.lossy_quality,
            options.keep_orientation, options.keep_color)
        if candidate is not None and len(candidate) < len(data):
            data, tool = candidate, name
        elif options.optimize:
            lossy_name = name
            candidate, optimize_name = optimize_bytes(
                data, ext, options.keep_color, options.max_effort,
                options.progressive)
            if candidate is not None and len(candidate) < len(data):
                data, tool = candidate, optimize_name
            else:
                tool = f"{lossy_name} not smaller; metadata only"
        else:
            tool = f"{name} not smaller; metadata only"
    elif options.optimize:
        candidate, name = optimize_bytes(
            data, ext, options.keep_color, options.max_effort,
            options.progressive)
        if candidate is not None and len(candidate) < len(data):
            data, tool = candidate, name

    return data, len(original), (data != original), tool


def _process_jxl(source: str, output: str, options: RunOptions) -> FileResult:
    try:
        jxl, orig_size, note = to_jxl_file(
            source, options.keep_color, options.keep_orientation,
            options.max_effort)
    except Exception as exc:
        return FileResult(source, None, 0, 0, "cjxl", "failed",
                          message=f"FAIL {source}: {exc}")

    if jxl is None:
        return FileResult(source, None, orig_size, 0, "cjxl",
                          f"skipped: {note}",
                          message=f"SKIP {source}: {note}")

    if os.path.exists(output):
        return FileResult(source, output, orig_size, 0, "cjxl",
                          "skipped: exists",
                          message=f"SKIP {source}: {output} already exists")

    saved = orig_size - len(jxl)
    digest = hashlib.sha256(jxl).hexdigest()
    if options.dry_run:
        return FileResult(
            source, output, orig_size, len(jxl), "cjxl", "dry-run",
            digest=digest, saved=saved,
            message=(f"{source}: {orig_size} -> {len(jxl)} bytes "
                     f"(saved {saved}) [cjxl] -> {output}"))

    try:
        write_file_atomic(output, jxl, keep_stat_from=source)
        if options.replace and not options.output_dir:
            if options.backup:
                shutil.copy2(source, source + ".bak")
            os.remove(source)
    except Exception as exc:
        return FileResult(
            source, output, orig_size, len(jxl), "cjxl", "failed",
            digest=digest,
            message=f"WRITE FAIL {source}: {exc}")

    return FileResult(
        source, output, orig_size, len(jxl), "cjxl", "written",
        digest=digest, saved=saved,
        message=(f"{source}: {orig_size} -> {len(jxl)} bytes "
                 f"(saved {saved}) [cjxl] -> {output}"))


def _process_standard(source: str, output: str, options: RunOptions) -> FileResult:
    try:
        new_data, orig_size, changed, tool = process_file(source, options)
    except Exception as exc:
        return FileResult(source, None, 0, 0, "metadata", "failed",
                          message=f"FAIL {source}: {exc}")

    saved = orig_size - len(new_data)

    skip_reason = None
    if options.only_if_smaller and (not changed or len(new_data) >= orig_size):
        skip_reason = "no smaller result"
    elif options.skip_clean and not changed:
        skip_reason = "already clean"
    if skip_reason:
        return FileResult(
            source, None, orig_size, 0, tool, f"skipped: {skip_reason}",
            message=f"SKIP {source}: {skip_reason}")

    if options.verify and not verify_lossless(
            source, new_data, options.keep_color, options.keep_orientation):
        return FileResult(
            source, None, orig_size, len(new_data), tool, "verify-failed",
            message=(f"VERIFY FAILED {source}: "
                     "pixels differ or output is invalid!"))

    digest = hashlib.sha256(new_data).hexdigest()
    if options.dry_run:
        return FileResult(
            source, output, orig_size, len(new_data), tool, "dry-run",
            digest=digest, saved=saved,
            message=(f"{source}: {orig_size} -> {len(new_data)} bytes "
                     f"(saved {saved}) [{tool}]"))

    try:
        if options.backup and output == source:
            shutil.copy2(source, source + ".bak")
        write_file_atomic(output, new_data, keep_stat_from=source)
    except Exception as exc:
        return FileResult(
            source, output, orig_size, len(new_data), tool, "failed",
            digest=digest,
            message=f"WRITE FAIL {source}: {exc}")

    return FileResult(
        source, output, orig_size, len(new_data), tool, "written",
        digest=digest, saved=saved,
        message=(f"{source}: {orig_size} -> {len(new_data)} bytes "
                 f"(saved {saved}) [{tool}] -> {output}"))


def process_one(source: str, output: str, options: RunOptions) -> FileResult:
    """Process a single planned file without printing or touching the UI."""
    if (options.skip_existing
            and canonical_path(source) != canonical_path(output)
            and os.path.exists(output)):
        try:
            source_size = os.path.getsize(source)
        except OSError:
            source_size = 0
        return FileResult(
            source, output, source_size, 0, "skip", "skipped: exists",
            message=f"SKIP {source}: {output} already exists")
    if options.to_jxl:
        return _process_jxl(source, output, options)
    return _process_standard(source, output, options)


def _record_result(summary: RunSummary, result: FileResult,
                   options: RunOptions, reporter: Reporter,
                   manifest_dir: str) -> None:
    if options.manifest:
        summary.manifest_rows.append(manifest_row(
            result.source, result.output or "", result.source_size,
            result.output_size, result.digest, result.tool, result.status,
            manifest_dir))

    if result.status in ("written", "dry-run"):
        summary.ok += 1
        summary.saved += result.saved
    elif result.status.startswith("skipped:"):
        summary.skipped += 1
    else:
        summary.failed += 1

    if result.message:
        if result.status in ("failed", "verify-failed"):
            reporter.error(result.message)
        else:
            reporter.log(result.message)


def _iter_results(entries, planned_outputs, options: RunOptions,
                  reporter: Reporter, cancel_event):
    """Yield ``(source, result)`` in input order.

    ``jobs == 1`` processes serially.  With more jobs, a small pool of daemon
    threads claims inputs in order while results are buffered and yielded in
    input order, keeping logs and manifests deterministic.
    """
    total = len(entries)
    jobs = max(1, min(options.jobs, total))

    if jobs == 1:
        for index, ((source, _base_dir), output) in enumerate(
                zip(entries, planned_outputs)):
            if cancel_event is not None and cancel_event.is_set():
                return
            reporter.progress(index, total, os.path.basename(source))
            yield source, process_one(source, output, options)
        return

    done_queue: "queue.Queue" = queue.Queue()
    lock = threading.Lock()
    state = {"next": 0, "stopped": False}

    def worker():
        try:
            while True:
                with lock:
                    if state["stopped"] or state["next"] >= total:
                        return
                    if cancel_event is not None and cancel_event.is_set():
                        state["stopped"] = True
                        return
                    index = state["next"]
                    state["next"] += 1

                source, _base_dir = entries[index]
                output = planned_outputs[index]
                try:
                    result = process_one(source, output, options)
                except BaseException as exc:
                    result = FileResult(
                        source, output, 0, 0, "", "failed",
                        message=f"FAIL {source}: {exc}")
                done_queue.put((index, source, result))
        finally:
            done_queue.put(None)

    threads = [
        threading.Thread(target=worker, daemon=True,
                         name=f"jxlerate-worker-{number}")
        for number in range(jobs)
    ]
    for thread in threads:
        thread.start()

    buffered = {}
    next_index = 0
    completed = 0
    finished_workers = 0
    try:
        while finished_workers < len(threads):
            item = done_queue.get()
            if item is None:
                finished_workers += 1
                continue
            index, source, result = item
            completed += 1
            reporter.progress(completed, total, os.path.basename(source))
            buffered[index] = result
            while next_index in buffered:
                yield entries[next_index][0], buffered.pop(next_index)
                next_index += 1

        # Every result arrived before its worker's sentinel; flush any tail
        # that was waiting on a slower earlier file.
        while next_index in buffered:
            yield entries[next_index][0], buffered.pop(next_index)
            next_index += 1
    finally:
        # Stop claiming new work if the consumer closes early (for example
        # because a reporter raised).
        with lock:
            state["stopped"] = True


def run_batch(options: RunOptions, reporter: Reporter,
              cancel_event=None) -> RunSummary:
    """Run a complete batch through the shared pipeline."""
    options = options.normalized()
    output_dir = options.output_dir
    exclude_dir = output_dir

    dir_inputs = [p for p in options.inputs if os.path.isdir(p)]
    prefix_roots = len(dir_inputs) > 1

    entries = collect_file_entries(
        options.inputs, options.recursive, exclude_dir)
    if not entries and not (options.passthrough and output_dir):
        return RunSummary(no_images=True, output_dir=output_dir)

    passthrough_entries = []
    if options.passthrough and output_dir:
        passthrough_entries = collect_passthrough_entries(
            options.inputs, options.recursive, prefix_roots, exclude_dir)

    planned_outputs = [
        plan_output_path(fp, base_dir, output_dir, options.suffix,
                         prefix_roots, to_jxl=options.to_jxl)
        for fp, base_dir in entries
    ]

    output_pairs = [(fp, out) for (fp, _base), out
                    in zip(entries, planned_outputs)]
    if passthrough_entries:
        output_pairs.extend(
            (src, os.path.join(output_dir, rel))
            for src, rel in passthrough_entries)
    source_paths = ([fp for fp, _ in entries] +
                    [src for src, _ in passthrough_entries])

    try:
        validate_manifest_location(options.manifest, options.inputs)
        validate_output_plan(output_pairs, source_paths, options.manifest)
    except ValueError as exc:
        return RunSummary(failed=1, error=str(exc), output_dir=output_dir)

    summary = RunSummary(output_dir=output_dir)
    manifest_dir = (os.path.dirname(os.path.abspath(options.manifest))
                    if options.manifest else "")

    reporter.start(len(entries))
    processed = 0
    for source, result in _iter_results(
            entries, planned_outputs, options, reporter, cancel_event):
        _record_result(summary, result, options, reporter, manifest_dir)
        processed += 1

    if (cancel_event is not None and cancel_event.is_set()
            and processed < len(entries)):
        summary.cancelled = True

    if summary.cancelled:
        reporter.log("Cancelled by user.")
        return summary

    if passthrough_entries:
        if options.dry_run:
            pending = len(passthrough_entries)
            if options.skip_existing:
                pending = sum(
                    1 for _src, rel in passthrough_entries
                    if not os.path.exists(os.path.join(output_dir, rel)))
            reporter.notice(
                f"Passthrough: would copy {pending} non-image file(s).")
        else:
            try:
                count = copy_passthrough(
                    passthrough_entries, output_dir, options.skip_existing)
                reporter.notice(
                    f"Passthrough: copied {count} non-image file(s).")
            except Exception as exc:
                reporter.error(f"PASSTHROUGH FAIL: {exc}")
                summary.failed += 1

    reporter.progress(len(entries), len(entries), "")
    return summary

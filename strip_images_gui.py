#!/usr/bin/env python3
"""GUI front-end for strip_images: losslessly strip metadata from PNG/JPEG files."""
import os
import queue
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Reuse the core logic from strip_images.py
import importlib.util
_SPEC = importlib.util.spec_from_file_location(
    "strip_images", os.path.join(os.path.dirname(os.path.abspath(__file__)), "strip_images.py"))
_si = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_si)

APP_TITLE = "Strip Image Metadata"
APP_VERSION = getattr(_si, "__version__", "unknown")
DONE = "__done__"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("740x600")
        self.minsize(620, 500)

        self.files = []
        self.worker = None
        self.log_queue = queue.Queue()

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # --- file list ---
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, **pad)

        ttk.Label(top, text="Files to process:").pack(anchor="w")
        list_frame = ttk.Frame(top)
        list_frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_row = ttk.Frame(top)
        btn_row.pack(fill="x", pady=4)
        ttk.Button(btn_row, text="Add Files", command=self.add_files).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Add Folder", command=self.add_folder).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Clear", command=self.clear_files).pack(side="left", padx=2)

        # --- options ---
        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", **pad)

        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Recurse into folders", variable=self.recursive_var).grid(
            row=0, column=0, sticky="w", padx=4, pady=2)

        self.backup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Keep .bak of originals", variable=self.backup_var).grid(
            row=0, column=1, sticky="w", padx=4, pady=2)

        self.verify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Verify pixels unchanged", variable=self.verify_var).grid(
            row=1, column=0, sticky="w", padx=4, pady=2)

        self.keep_color_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Keep color info", variable=self.keep_color_var).grid(
            row=1, column=1, sticky="w", padx=4, pady=2)

        self.dryrun_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Dry run (report only)", variable=self.dryrun_var).grid(
            row=2, column=0, sticky="w", padx=4, pady=2)

        self.passthrough_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Passthrough other files", variable=self.passthrough_var).grid(
            row=2, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(opts, text="Suffix:").grid(row=3, column=0, sticky="w", padx=4, pady=2)
        self.suffix_var = tk.StringVar(value="")
        ttk.Entry(opts, textvariable=self.suffix_var, width=16).grid(
            row=3, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(opts, text="Output folder (blank = in place):").grid(
            row=4, column=0, sticky="w", padx=4, pady=2)
        self.outdir_var = tk.StringVar(value="")
        ttk.Entry(opts, textvariable=self.outdir_var, width=40).grid(
            row=4, column=1, sticky="we", padx=4, pady=2)
        ttk.Button(opts, text="Browse", command=self.browse_outdir).grid(
            row=4, column=2, padx=4, pady=2)
        opts.columnconfigure(1, weight=1)

        # --- run ---
        run_row = ttk.Frame(self)
        run_row.pack(fill="x", **pad)
        self.run_btn = ttk.Button(run_row, text="Strip Metadata", command=self.run)
        self.run_btn.pack(side="left", padx=2)
        self.progress = ttk.Progressbar(run_row, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)

        self.hint = ttk.Label(
            self,
            text="Tip: leave Output folder blank to overwrite in place; set it to keep copies.",
            foreground="#555")
        self.hint.pack(anchor="w", padx=6)

        # --- log ---
        ttk.Label(self, text="Log:").pack(anchor="w", padx=6)
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=10, wrap="word", state="disabled")
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.config(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    # -- logging / queue ----------------------------------------------------
    def log_msg(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if isinstance(msg, tuple) and msg and msg[0] == DONE:
                    self._finish(msg[1])
                else:
                    self.log_msg(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # -- file selection -----------------------------------------------------
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG"), ("All files", "*.*")])
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", p)

    def add_folder(self):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            if d not in self.files:
                self.files.append(d)
                self.listbox.insert("end", d + "  [folder]")

    def remove_selected(self):
        for idx in list(self.listbox.curselection())[::-1]:
            self.listbox.delete(idx)
            del self.files[idx]

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, "end")

    def browse_outdir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.outdir_var.set(d)

    # -- run ----------------------------------------------------------------
    def run(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Already running.")
            return
        if not self.files:
            messagebox.showwarning("No files", "Add at least one file or folder first.")
            return

        # Read Tk values in the main thread (Tk is not thread-safe).
        options = {
            "inputs": list(self.files),
            "recursive": self.recursive_var.get(),
            "outdir": self.outdir_var.get().strip() or None,
            "suffix": self.suffix_var.get().strip(),
            "backup": self.backup_var.get(),
            "verify": self.verify_var.get(),
            "keep_color": self.keep_color_var.get(),
            "dry": self.dryrun_var.get(),
            "passthrough": self.passthrough_var.get(),
        }
        if options["passthrough"] and not options["outdir"]:
            messagebox.showwarning("Passthrough", "Passthrough requires an Output folder.")
            return
        if options["verify"] and not _si.have_pillow():
            messagebox.showwarning("Verify", "Pillow is not installed; disabling verification.")
            options["verify"] = False

        self.run_btn.config(state="disabled")
        self.progress.start(10)
        self.worker = threading.Thread(target=self._run_worker, args=(options,), daemon=True)
        self.worker.start()

    def _run_worker(self, options):
        try:
            self.last_summary = self._process(options)
        except Exception as e:
            self.log_queue.put(f"ERROR: {e}")
            self.last_summary = None
        finally:
            self.log_queue.put((DONE, getattr(self, "last_summary", None)))

    def _process(self, options):
        inputs = options["inputs"]
        recursive = options["recursive"]
        outdir = os.path.abspath(options["outdir"]) if options["outdir"] else None
        suffix = options["suffix"]
        backup = options["backup"]
        verify = options["verify"]
        keep_color = options["keep_color"]
        dry = options["dry"]
        passthrough = options["passthrough"]

        exclude = outdir
        dir_inputs = [p for p in inputs if os.path.isdir(p)]
        prefix_roots = len(dir_inputs) > 1

        entries = _si.collect_file_entries(inputs, recursive, exclude)
        if not entries and not (passthrough and outdir):
            self.log_queue.put("No image files found.")
            return "none"

        passthrough_entries = []
        if passthrough and outdir:
            passthrough_entries = _si.collect_passthrough_entries(
                inputs, recursive, prefix_roots, exclude)

        self.log_queue.put(f"Processing {len(entries)} image file(s)...")
        total_saved = 0
        ok = 0
        fail = 0

        for fp, base_dir in entries:
            try:
                new_data, orig_size = _si.strip_file(fp, keep_color)
            except Exception as e:
                self.log_queue.put(f"FAIL {fp}: {e}")
                fail += 1
                continue

            if verify and not _si.verify_lossless(fp, new_data):
                self.log_queue.put(f"VERIFY FAILED {fp}: pixels differ or output invalid!")
                fail += 1
                continue

            saved = orig_size - len(new_data)
            total_saved += saved

            if outdir:
                out_path = _si.output_path_for(
                    fp, base_dir, outdir, suffix, prefix_roots)
            elif suffix:
                d = os.path.dirname(fp)
                name, ext = os.path.splitext(os.path.basename(fp))
                out_path = os.path.join(d, name + suffix + ext)
            else:
                out_path = fp

            if dry:
                self.log_queue.put(f"{fp}: {orig_size} -> {len(new_data)} bytes (saved {saved})")
                ok += 1
                continue

            try:
                if backup and out_path == fp:
                    import shutil
                    shutil.copy2(fp, fp + ".bak")
                _si.write_file_atomic(out_path, new_data)
                self.log_queue.put(
                    f"{fp}: {orig_size} -> {len(new_data)} bytes (saved {saved}) -> {out_path}")
                ok += 1
            except Exception as e:
                self.log_queue.put(f"WRITE FAIL {fp}: {e}")
                fail += 1

        if passthrough_entries:
            if dry:
                self.log_queue.put(
                    f"Passthrough: would copy {len(passthrough_entries)} non-image file(s).")
            else:
                try:
                    n = _si.copy_passthrough(passthrough_entries, outdir)
                    self.log_queue.put(f"Passthrough: copied {n} non-image file(s).")
                except Exception as e:
                    self.log_queue.put(f"PASSTHROUGH FAIL: {e}")
                    fail += 1

        self.log_queue.put(
            f"\nDone: {ok} ok, {fail} failed, total saved {total_saved} bytes")
        return (ok, fail, total_saved)

    def _finish(self, summary):
        self.progress.stop()
        self.run_btn.config(state="normal")
        if summary == "none":
            messagebox.showwarning("No files", "No image files found in the selected inputs.")
        elif isinstance(summary, tuple):
            ok, fail, saved = summary
            messagebox.showinfo(
                "Finished", f"Done: {ok} ok, {fail} failed.\nTotal saved: {saved} bytes.")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

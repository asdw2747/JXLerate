#!/usr/bin/env python3
"""GUI front-end for strip_images: losslessly strip metadata from image files."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
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

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "strip-images")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")

THEMES = {
    "dark": {
        "window": "#232629",
        "base": "#31363b",
        "alt_base": "#2b2e32",
        "text": "#eff0f1",
        "muted": "#a0a4a8",
        "disabled": "#6d7176",
        "select_bg": "#2a82da",
        "select_fg": "#ffffff",
        "border": "#4f565c",
        "button": "#3b4045",
        "button_active": "#4a5056",
        "trough": "#1b1e20",
        "accent": "#3daee9",
        "log_bg": "#1b1e20",
    },
    "light": {
        "window": "#f0f0f0",
        "base": "#ffffff",
        "alt_base": "#f7f7f7",
        "text": "#1a1a1a",
        "muted": "#555555",
        "disabled": "#9a9a9a",
        "select_bg": "#308cc6",
        "select_fg": "#ffffff",
        "border": "#c8c8c8",
        "button": "#e6e6e6",
        "button_active": "#dcdcdc",
        "trough": "#d9d9d9",
        "accent": "#308cc6",
        "log_bg": "#ffffff",
    },
}


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if data.get("theme") not in ("auto", "light", "dark"):
                data["theme"] = "auto"
            if not isinstance(data.get("options"), dict):
                data["options"] = {}
            return data
    except Exception:
        pass
    return {"theme": "auto", "options": {}}


def save_config(config):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


def detect_system_theme() -> str:
    """Return 'dark' or 'light' based on the desktop's preference."""
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=1.5)
        value = result.stdout.strip().strip("'").lower()
        if "dark" in value:
            return "dark"
        if value in ("default", "prefer-light", "light"):
            return "light"
    except Exception:
        pass

    try:
        path = os.path.expanduser("~/.config/gtk-3.0/settings.ini")
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("gtk-application-prefer-dark-theme"):
                    _, _, value = line.partition("=")
                    if value.strip().lower() in ("1", "true", "yes"):
                        return "dark"
    except Exception:
        pass

    try:
        if "dark" in os.environ.get("GTK_THEME", "").lower():
            return "dark"
    except Exception:
        pass

    return "light"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.config_data = load_config()
        self.saved_options = self.config_data.get("options", {})
        self.theme_mode = self.config_data.get("theme", "auto")
        self.resolved_theme = (
            detect_system_theme() if self.theme_mode == "auto" else self.theme_mode)
        if self.resolved_theme not in THEMES:
            self.resolved_theme = "light"

        self.title(f"{APP_TITLE} v{APP_VERSION}")
        geometry = self.config_data.get("geometry")
        self.geometry(geometry if isinstance(geometry, str) and _GEOMETRY_RE.match(geometry)
                      else "780x780")
        self.minsize(660, 640)
        self.configure(bg=THEMES[self.resolved_theme]["window"])

        self.files = []
        self.worker = None
        self.cancel_event = threading.Event()
        self.log_queue = queue.Queue()
        self.style = ttk.Style(self)
        self.theme_var = tk.StringVar(value=self.theme_mode)
        self.status_var = tk.StringVar(value="Ready")
        self.last_output_dir = None
        self._poll_id = None

        self._build_ui()
        self.apply_theme(self.theme_mode)
        self._poll_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Destroy>", self._on_destroy)

        self.bind_all("<Control-o>", lambda _e: self.add_files())
        self.bind_all("<Control-O>", lambda _e: self.add_folder())
        self.bind_all("<Control-Return>", lambda _e: self.run())
        self.bind_all("<Control-KP_Enter>", lambda _e: self.run())
        self.bind_all("<Control-t>", lambda _e: self._cycle_theme())
        self.bind_all("<Escape>", lambda _e: self.cancel_run())

    # -- config -------------------------------------------------------------
    def _opt(self, key, default):
        return self.saved_options.get(key, default)

    def _current_geometry(self):
        """Return the current geometry, ignoring the pre-map 1x1 value."""
        try:
            self.update_idletasks()
        except Exception:
            pass
        geom = self.geometry()
        match = re.match(r"^(\d+)x(\d+)", geom)
        if match and int(match.group(1)) >= 300 and int(match.group(2)) >= 300:
            return geom
        saved = self.config_data.get("geometry")
        return saved if isinstance(saved, str) and _GEOMETRY_RE.match(saved) else "780x780"

    def _save_settings(self):
        self.config_data["theme"] = self.theme_mode
        self.config_data["geometry"] = self._current_geometry()
        self.config_data["options"] = {
            "recursive": self.recursive_var.get(),
            "backup": self.backup_var.get(),
            "verify": self.verify_var.get(),
            "keep_color": self.keep_color_var.get(),
            "keep_orientation": self.keep_orientation_var.get(),
            "dryrun": self.dryrun_var.get(),
            "passthrough": self.passthrough_var.get(),
            "skip_clean": self.skip_clean_var.get(),
            "only_smaller": self.only_smaller_var.get(),
            "suffix": self.suffix_var.get(),
            "outdir": self.outdir_var.get(),
        }
        save_config(self.config_data)

    def _on_close(self):
        self._save_settings()
        self.destroy()

    def _on_destroy(self, event):
        if event.widget is self and self._poll_id:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    # -- UI construction ----------------------------------------------------
    def _add_check(self, parent, text, variable, row, column):
        widget = tk.Checkbutton(
            parent, text=text, variable=variable, anchor="w",
            highlightthickness=0, borderwidth=0, takefocus=True)
        widget.grid(row=row, column=column, sticky="w", padx=4, pady=2)
        self._check_widgets.append(widget)
        return widget

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, **pad)

        self.files_label = ttk.Label(top, text="Files to process: 0")
        self.files_label.pack(anchor="w")
        list_frame = ttk.Frame(top)
        list_frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, activestyle="none")
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

        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", **pad)

        self._check_widgets = []
        self._radio_widgets = []

        self.recursive_var = tk.BooleanVar(value=bool(self._opt("recursive", True)))
        self._add_check(opts, "Recurse into folders", self.recursive_var, 0, 0)

        self.backup_var = tk.BooleanVar(value=bool(self._opt("backup", False)))
        self._add_check(opts, "Keep .bak of originals", self.backup_var, 0, 1)

        self.verify_var = tk.BooleanVar(value=bool(self._opt("verify", True)))
        self._add_check(opts, "Verify pixels unchanged", self.verify_var, 1, 0)

        self.keep_color_var = tk.BooleanVar(value=bool(self._opt("keep_color", False)))
        self._add_check(opts, "Keep color info", self.keep_color_var, 1, 1)

        self.dryrun_var = tk.BooleanVar(value=bool(self._opt("dryrun", False)))
        self._add_check(opts, "Dry run (report only)", self.dryrun_var, 2, 0)

        self.passthrough_var = tk.BooleanVar(value=bool(self._opt("passthrough", False)))
        self._add_check(opts, "Passthrough other files", self.passthrough_var, 2, 1)

        self.skip_clean_var = tk.BooleanVar(value=bool(self._opt("skip_clean", False)))
        self._add_check(opts, "Skip unchanged files", self.skip_clean_var, 3, 0)

        self.only_smaller_var = tk.BooleanVar(value=bool(self._opt("only_smaller", False)))
        self._add_check(opts, "Only write if smaller", self.only_smaller_var, 3, 1)

        self.keep_orientation_var = tk.BooleanVar(
            value=bool(self._opt("keep_orientation", True)))
        self._add_check(opts, "Preserve orientation", self.keep_orientation_var, 4, 0)

        theme_row = ttk.Frame(opts)
        theme_row.grid(row=5, column=0, columnspan=3, sticky="w", padx=4, pady=2)
        ttk.Label(theme_row, text="Theme:").pack(side="left", padx=(0, 6))
        for value, label in (("auto", "Auto"), ("light", "Light"), ("dark", "Dark")):
            radio = tk.Radiobutton(
                theme_row, text=label, value=value, variable=self.theme_var,
                command=self._on_theme_change, anchor="w",
                highlightthickness=0, borderwidth=0, takefocus=True)
            radio.pack(side="left", padx=4)
            self._radio_widgets.append(radio)

        ttk.Label(opts, text="Suffix:").grid(row=6, column=0, sticky="w", padx=4, pady=2)
        self.suffix_var = tk.StringVar(value=str(self._opt("suffix", "")))
        ttk.Entry(opts, textvariable=self.suffix_var, width=16).grid(
            row=6, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(opts, text="Output folder (blank = in place):").grid(
            row=7, column=0, sticky="w", padx=4, pady=2)
        self.outdir_var = tk.StringVar(value=str(self._opt("outdir", "")))
        ttk.Entry(opts, textvariable=self.outdir_var, width=40).grid(
            row=7, column=1, sticky="we", padx=4, pady=2)
        ttk.Button(opts, text="Browse", command=self.browse_outdir).grid(
            row=7, column=2, padx=4, pady=2)
        ttk.Button(opts, text="Open", command=self.open_output).grid(
            row=7, column=3, padx=(0, 4), pady=2)
        opts.columnconfigure(1, weight=1)

        run_row = ttk.Frame(self)
        run_row.pack(fill="x", **pad)
        self.run_btn = ttk.Button(run_row, text="Strip Metadata", command=self.run)
        self.run_btn.pack(side="left", padx=2)
        self.progress = ttk.Progressbar(run_row, mode="determinate", maximum=1, value=0)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.cancel_btn = ttk.Button(run_row, text="Cancel", command=self.cancel_run,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=2)

        self.hint = ttk.Label(self, text="")
        self.hint.pack(anchor="w", padx=6)

        self.status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        self.status.pack(fill="x", padx=6, pady=(0, 4))

        ttk.Label(self, text="Log:").pack(anchor="w", padx=6)
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=9, wrap="word", state="disabled")
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.config(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    # -- theming ------------------------------------------------------------
    def apply_theme(self, mode):
        self.theme_mode = mode
        resolved = detect_system_theme() if mode == "auto" else mode
        if resolved not in THEMES:
            resolved = "light"
        self.resolved_theme = resolved
        p = THEMES[resolved]

        self.configure(bg=p["window"])
        s = self.style
        s.theme_use("clam")

        s.configure(
            ".", background=p["window"], foreground=p["text"],
            fieldbackground=p["base"], bordercolor=p["border"],
            lightcolor=p["window"], darkcolor=p["window"],
            troughcolor=p["trough"], focuscolor=p["accent"],
            selectbackground=p["select_bg"], selectforeground=p["select_fg"])
        s.configure("TFrame", background=p["window"])
        s.configure("TLabel", background=p["window"], foreground=p["text"])
        s.configure("TLabelframe", background=p["window"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["border"],
                    darkcolor=p["border"])
        s.configure("TLabelframe.Label", background=p["window"], foreground=p["text"])
        s.configure("TButton", background=p["button"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["window"],
                    darkcolor=p["window"], focusthickness=1)
        s.map("TButton",
              background=[("pressed", p["button_active"]), ("active", p["button_active"])],
              foreground=[("disabled", p["disabled"])])
        s.configure("TEntry", fieldbackground=p["base"], foreground=p["text"],
                    insertcolor=p["text"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"])
        s.map("TEntry",
              fieldbackground=[("disabled", p["alt_base"])],
              foreground=[("disabled", p["disabled"])])
        s.configure("TProgressbar", background=p["accent"], troughcolor=p["trough"],
                    bordercolor=p["border"], lightcolor=p["accent"],
                    darkcolor=p["accent"])
        s.configure("TScrollbar", background=p["button"], troughcolor=p["trough"],
                    bordercolor=p["border"], arrowcolor=p["text"],
                    lightcolor=p["window"], darkcolor=p["window"], relief="flat")
        s.map("TScrollbar", background=[("active", p["button_active"])])

        self.listbox.configure(
            background=p["base"], foreground=p["text"],
            selectbackground=p["select_bg"], selectforeground=p["select_fg"],
            highlightbackground=p["border"], highlightcolor=p["accent"],
            highlightthickness=1, borderwidth=0, relief="flat",
            disabledforeground=p["disabled"])
        self.log.configure(
            background=p["log_bg"], foreground=p["text"],
            insertbackground=p["text"],
            selectbackground=p["select_bg"], selectforeground=p["select_fg"],
            highlightbackground=p["border"], highlightcolor=p["accent"],
            highlightthickness=1, borderwidth=0, relief="flat")

        for widget in self._check_widgets + self._radio_widgets:
            widget.configure(
                background=p["window"], foreground=p["text"],
                activebackground=p["window"], activeforeground=p["text"],
                selectcolor=p["base"], disabledforeground=p["disabled"],
                highlightbackground=p["window"], highlightcolor=p["accent"])

        label = mode if mode != "auto" else f"auto ({resolved})"
        self.hint.configure(
            text=(f"Theme: {label}.  Tip: leave Output folder blank to overwrite "
                  "in place; set it to keep copies."),
            foreground=p["muted"])

    def _on_theme_change(self):
        mode = self.theme_var.get()
        self.apply_theme(mode)
        self.config_data["theme"] = mode
        save_config(self.config_data)

    def _cycle_theme(self):
        order = ["auto", "dark", "light"]
        try:
            index = order.index(self.theme_var.get())
        except ValueError:
            index = 0
        self.theme_var.set(order[(index + 1) % len(order)])
        self._on_theme_change()

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
                if isinstance(msg, tuple):
                    if msg and msg[0] == DONE:
                        self._finish(msg[1])
                    elif msg and msg[0] == "progress":
                        _, index, total, name = msg
                        self._set_progress(index, total, name)
                    else:
                        self.log_msg(str(msg))
                else:
                    self.log_msg(msg)
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._poll_queue)

    def _set_progress(self, index, total, name):
        total = max(total, 1)
        self.progress.configure(maximum=total, value=index)
        if name:
            self.status_var.set(f"{index}/{total}  {name}")
        else:
            self.status_var.set(f"{index}/{total}")

    # -- file selection -----------------------------------------------------
    def _update_count(self):
        self.files_label.config(text=f"Files to process: {len(self.files)}")

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.gif *.PNG *.JPG *.JPEG *.WEBP *.GIF"),
                ("All files", "*.*"),
            ])
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", p)
        self._update_count()

    def add_folder(self):
        d = filedialog.askdirectory(title="Select folder")
        if d and d not in self.files:
            self.files.append(d)
            self.listbox.insert("end", d + "  [folder]")
        self._update_count()

    def remove_selected(self):
        for idx in list(self.listbox.curselection())[::-1]:
            self.listbox.delete(idx)
            del self.files[idx]
        self._update_count()

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, "end")
        self._update_count()

    def browse_outdir(self):
        d = filedialog.askdirectory(title="Select output folder")
        if d:
            self.outdir_var.set(d)

    def open_output(self):
        d = self.outdir_var.get().strip() or self.last_output_dir
        if not d or not os.path.isdir(d):
            messagebox.showinfo("Output folder", "No output folder to open yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(d)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror("Open folder", str(e))

    # -- run ----------------------------------------------------------------
    def _gather_options(self):
        return {
            "inputs": list(self.files),
            "recursive": self.recursive_var.get(),
            "outdir": self.outdir_var.get().strip() or None,
            "suffix": self.suffix_var.get().strip(),
            "backup": self.backup_var.get(),
            "verify": self.verify_var.get(),
            "keep_color": self.keep_color_var.get(),
            "keep_orientation": self.keep_orientation_var.get(),
            "dry": self.dryrun_var.get(),
            "passthrough": self.passthrough_var.get(),
            "skip_clean": self.skip_clean_var.get(),
            "only_smaller": self.only_smaller_var.get(),
        }

    def run(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Already running.")
            return
        if not self.files:
            messagebox.showwarning("No files", "Add at least one file or folder first.")
            return

        options = self._gather_options()
        if options["passthrough"] and not options["outdir"]:
            messagebox.showwarning("Passthrough", "Passthrough requires an Output folder.")
            return
        if options["verify"] and not _si.have_pillow():
            messagebox.showwarning("Verify", "Pillow is not installed; disabling verification.")
            options["verify"] = False
        if not options["outdir"] and not options["dry"]:
            if not messagebox.askyesno(
                    "Overwrite originals?",
                    "No output folder is set, so the original files will be modified "
                    "in place.\n\nContinue?"):
                return

        self._save_settings()
        self.cancel_event.clear()
        self.run_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress.configure(maximum=1, value=0)
        self.status_var.set("Starting...")
        self.worker = threading.Thread(target=self._run_worker, args=(options,), daemon=True)
        self.worker.start()

    def cancel_run(self):
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.cancel_btn.config(state="disabled")
            self.status_var.set("Cancelling...")

    def _run_worker(self, options):
        try:
            summary = self._process(options)
        except Exception as e:
            self.log_queue.put(f"ERROR: {e}")
            summary = None
        finally:
            self.log_queue.put((DONE, summary))

    def _process(self, options):
        inputs = options["inputs"]
        recursive = options["recursive"]
        outdir = os.path.abspath(options["outdir"]) if options["outdir"] else None
        suffix = options["suffix"]
        backup = options["backup"]
        verify = options["verify"]
        keep_color = options["keep_color"]
        keep_orientation = options["keep_orientation"]
        dry = options["dry"]
        passthrough = options["passthrough"]
        skip_clean = options["skip_clean"]
        only_smaller = options["only_smaller"]

        exclude = outdir
        dir_inputs = [p for p in inputs if os.path.isdir(p)]
        prefix_roots = len(dir_inputs) > 1

        entries = _si.collect_file_entries(inputs, recursive, exclude)
        if outdir:
            self.last_output_dir = outdir
        if not entries and not (passthrough and outdir):
            self.log_queue.put("No image files found.")
            return "none"

        passthrough_entries = []
        if passthrough and outdir:
            passthrough_entries = _si.collect_passthrough_entries(
                inputs, recursive, prefix_roots, exclude)

        total = len(entries)
        self.log_queue.put(f"Processing {total} image file(s)...")
        self.log_queue.put(("progress", 0, total, ""))
        total_saved = 0
        ok = 0
        skipped = 0
        fail = 0
        cancelled = False

        for index, (fp, base_dir) in enumerate(entries, 1):
            if self.cancel_event.is_set():
                cancelled = True
                break

            self.log_queue.put(("progress", index - 1, total, os.path.basename(fp)))
            try:
                new_data, orig_size, changed = _si.strip_file(
                    fp, keep_color, keep_orientation)
            except Exception as e:
                self.log_queue.put(f"FAIL {fp}: {e}")
                fail += 1
                continue

            saved = orig_size - len(new_data)

            skip_reason = None
            if only_smaller and (not changed or len(new_data) >= orig_size):
                skip_reason = "no smaller result"
            elif skip_clean and not changed:
                skip_reason = "already clean"
            if skip_reason:
                self.log_queue.put(f"SKIP {fp}: {skip_reason}")
                skipped += 1
                continue

            if verify and not _si.verify_lossless(fp, new_data):
                self.log_queue.put(f"VERIFY FAILED {fp}: pixels differ or output invalid!")
                fail += 1
                continue

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
                    shutil.copy2(fp, fp + ".bak")
                _si.write_file_atomic(out_path, new_data, keep_stat_from=fp)
                self.log_queue.put(
                    f"{fp}: {orig_size} -> {len(new_data)} bytes (saved {saved}) -> {out_path}")
                ok += 1
            except Exception as e:
                self.log_queue.put(f"WRITE FAIL {fp}: {e}")
                fail += 1

        if cancelled:
            self.log_queue.put("Cancelled by user.")
        else:
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

        if not cancelled:
            self.log_queue.put(("progress", total, total, ""))
        self.log_queue.put(
            f"\nDone: {ok} ok, {skipped} skipped, {fail} failed, "
            f"total saved {total_saved} bytes")
        return (ok, skipped, fail, total_saved, cancelled)

    def _finish(self, summary):
        self.run_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self._save_settings()

        if summary == "none":
            self.status_var.set("No image files found")
            messagebox.showwarning("No files", "No image files found in the selected inputs.")
        elif isinstance(summary, tuple):
            ok, skipped, fail, saved, cancelled = summary
            state = "Cancelled" if cancelled else "Finished"
            self.status_var.set(
                f"{state}: {ok} ok, {skipped} skipped, {fail} failed, "
                f"{saved} bytes saved")
            if cancelled:
                messagebox.showinfo(
                    "Cancelled",
                    f"Stopped after {ok} file(s).\n"
                    f"Skipped: {skipped}   Failed: {fail}\nSaved: {saved} bytes.")
            else:
                messagebox.showinfo(
                    "Finished",
                    f"Done: {ok} ok, {skipped} skipped, {fail} failed.\n"
                    f"Total saved: {saved} bytes.")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

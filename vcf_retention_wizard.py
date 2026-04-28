#!/usr/bin/env python3
"""
VCF Backup Retention - Configuration Wizard
============================================
Interactive wizard that builds a JSON config for vcf_backup_retention.py.

Two paths:
  - Simple   - guided setup for typical VCF deployments
  - Advanced - full control: custom targets, regex patterns, mtime fallback,
               per-target overrides

Cross-platform: Linux, macOS, Windows 10+. Pure Python 3.8+ except for
'colorama' (required) and 'pyreadline3' (optional, recommended on Windows
for editable defaults in prompts).

Install:
    pip install colorama
    pip install pyreadline3   # Windows only, optional but recommended

Usage:
    python vcf_retention_wizard.py
    python vcf_retention_wizard.py -o my-config.json
"""

import argparse
import json
import os
import platform
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Color setup
# ---------------------------------------------------------------------------
try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
except ImportError:
    sys.stderr.write(
        "ERROR: 'colorama' is required. Install with:\n"
        "       pip install colorama\n"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Readline (legacy) - kept for environments where it works, but the editable
# prompt below uses raw terminal mode instead, which is more reliable
# across Linux/Windows.
# ---------------------------------------------------------------------------
HAS_READLINE = False
try:
    import readline  # built-in on Linux/macOS
    HAS_READLINE = True
except ImportError:
    try:
        import pyreadline3 as readline  # noqa: F401
        HAS_READLINE = True
    except ImportError:
        readline = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IS_WINDOWS = (os.name == "nt")

# Components per VCF version (preset name + display label)
COMPONENTS = {
    "vcf52": [
        ("sddc_manager", "SDDC Manager (.tar.gz files)"),
        ("nsx",          "NSX-T (timestamped folders)"),
        ("vcenter",      "vCenter Server (VAMI sn_... folders)"),
    ],
    "vcf9": [
        ("sddc_manager", "SDDC Manager (.tar.gz files)"),
        ("nsx",          "NSX (timestamped folders)"),
        ("vcenter",      "vCenter Server (VAMI sn_... folders)"),
        ("vcf9_fleet",   "Fleet Mgmt (Fleet Manager + Identity Broker + Automation)"),
    ],
}

# Default retention values per preset (sensible starting points)
DEFAULT_RETENTION = {
    "sddc_manager": {"keep_days": 30, "keep_minimum": 10},
    "nsx":          {"keep_days": 14, "keep_minimum": 7},
    "vcenter":      {"keep_days": 30, "keep_minimum": 7},
    "vcf9_fleet":   {"keep_days": 14, "keep_minimum": 5},
}

# Default folder name suggestions per preset
DEFAULT_SUBPATH = {
    "sddc_manager": "sddc-manager",
    "nsx":          "nsx",
    "vcenter":      "vcenter",
    "vcf9_fleet":   "fleet",
}

# Examples shown for custom file-mode patterns
FILE_REGEX_EXAMPLES = [
    {
        "label":    "Single timestamp like '...-2025-04-25-03-00-00.tar.gz'",
        "pattern":  r"^.+-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\..+$",
        "formats":  ["%Y-%m-%d-%H-%M-%S"],
        "matches":  "myapp-2025-04-25-03-00-00.tar.gz",
    },
    {
        "label":    "Date+time as separate groups like '..._20250425_030000.sql.gz'",
        "pattern":  r"^.+_(\d{8})_(\d{6})\..+$",
        "formats":  ["%Y%m%d%H%M%S"],
        "matches":  "prod_db_20250425_030000.sql.gz",
    },
    {
        "label":    "ISO timestamp like '...-2025-04-25T03-00-00.zip'",
        "pattern":  r"^.+-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})\..+$",
        "formats":  ["%Y-%m-%dT%H-%M-%S"],
        "matches":  "myapp-2025-04-25T03-00-00.zip",
    },
    {
        "label":    "Date only like '...-2025-04-25.tar.bz2'",
        "pattern":  r"^.+-(\d{4}-\d{2}-\d{2})\..+$",
        "formats":  ["%Y-%m-%d"],
        "matches":  "logs-2025-04-25.tar.bz2",
    },
    {
        "label":    "Compact timestamp like '...20250425-030000.tgz'",
        "pattern":  r"^.+(\d{8}-\d{6})\..+$",
        "formats":  ["%Y%m%d-%H%M%S"],
        "matches":  "backup-20250425-030000.tgz",
    },
    {
        "label":    "No date in name - use file mtime",
        "pattern":  r"^backup.+\..+$",
        "formats":  [],
        "matches":  "backup-final.tar.gz (age from mtime)",
    },
    {
        "label":    "Custom - write your own pattern",
        "pattern":  None,
        "formats":  None,
        "matches":  None,
    },
]

# Default log path - always Linux-style; the backup server runs Linux even
# if the wizard happens to be run on Windows.
def default_log_path() -> str:
    return "/var/log/vcf-backup-retention.log"


def default_backup_root() -> str:
    return "/backup"


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def c_header(text: str) -> str:
    return f"{Style.BRIGHT}{Fore.CYAN}{text}{Style.RESET_ALL}"

def c_question(text: str) -> str:
    return f"{Style.BRIGHT}{Fore.WHITE}{text}{Style.RESET_ALL}"

def c_hint(text: str) -> str:
    return f"{Style.DIM}{text}{Style.RESET_ALL}"

def c_value(text: str) -> str:
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}"

def c_number(text: str) -> str:
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"

def c_error(text: str) -> str:
    return f"{Fore.RED}{text}{Style.RESET_ALL}"

def c_success(text: str) -> str:
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}"

def c_warn(text: str) -> str:
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"


def banner():
    print(c_header("=" * 70))
    print(c_header("    VCF Backup Retention - Configuration Wizard"))
    print(c_header("=" * 70))
    print()


def section(title: str):
    print()
    print(c_header(f"--- {title} ---"))
    print()


# ---------------------------------------------------------------------------
# Editable input (raw terminal mode)
# ---------------------------------------------------------------------------
# Prints a prompt with the default value already typed at the cursor, so the
# user can press Enter to accept, or use backspace / typing to edit. Works
# without external dependencies via termios on Linux/macOS and msvcrt on
# Windows. Falls back to a [bracket] prompt when stdin/stdout aren't a TTY.

def _editable_input(prompt_str: str, default: str) -> str:
    """Show `prompt_str + default` with cursor at the end of `default`;
    let user edit and press Enter; return the final string.

    Falls back to the classic '[default]: ' prompt when raw mode isn't
    available (non-TTY input/output, or platform without termios/msvcrt)."""

    # No TTY - bracket fallback
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raw = input(f"{prompt_str} {c_hint(f'[{default}]')}: ").strip()
        return raw or default

    if os.name == "nt":
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            raw = input(f"{prompt_str} {c_hint(f'[{default}]')}: ").strip()
            return raw or default
        return _editable_input_windows(prompt_str, default)
    else:
        try:
            import termios, tty  # noqa: F401
        except ImportError:
            raw = input(f"{prompt_str} {c_hint(f'[{default}]')}: ").strip()
            return raw or default
        try:
            return _editable_input_unix(prompt_str, default)
        except OSError:
            # tcgetattr may fail on weird stdin; fall back gracefully
            sys.stdout.write("\n")
            raw = input(f"{prompt_str} {c_hint(f'[{default}]')}: ").strip()
            return raw or default


def _editable_input_unix(prompt_str: str, default: str) -> str:
    """Linux/macOS implementation of editable input using termios."""
    import termios

    sys.stdout.write(f"{prompt_str}: {default}")
    sys.stdout.flush()

    buf = list(default)

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = list(old_attrs)
    # Turn off canonical mode (line buffering) and echo. Keep ISIG so Ctrl-C
    # still works if needed; we also handle \x03 manually below for clarity.
    new_attrs[3] = new_attrs[3] & ~(termios.ICANON | termios.ECHO)

    try:
        termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                # EOF
                raise EOFError

            # --- Enter / Return ---
            if ch in ("\n", "\r"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(buf)

            # --- Backspace / DEL ---
            if ch in ("\x7f", "\x08"):
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue

            # --- Ctrl-C ---
            if ch == "\x03":
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise KeyboardInterrupt

            # --- Ctrl-D (EOF if buffer empty) ---
            if ch == "\x04":
                if not buf:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise EOFError
                continue

            # --- Ctrl-U: clear line ---
            if ch == "\x15":
                while buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                sys.stdout.flush()
                continue

            # --- Ctrl-W: delete previous word ---
            if ch == "\x17":
                while buf and buf[-1] == " ":
                    buf.pop()
                    sys.stdout.write("\b \b")
                while buf and buf[-1] != " ":
                    buf.pop()
                    sys.stdout.write("\b \b")
                sys.stdout.flush()
                continue

            # --- Escape sequences (arrow keys etc): consume and ignore ---
            if ch == "\x1b":
                # Common ANSI: ESC [ X (3 bytes total). Read up to 2 more.
                try:
                    nxt = sys.stdin.read(1)
                    if nxt == "[":
                        sys.stdin.read(1)
                except Exception:
                    pass
                continue

            # --- Printable character ---
            if ch.isprintable():
                buf.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()
            # else: ignore other control chars
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def _editable_input_windows(prompt_str: str, default: str) -> str:
    """Windows implementation of editable input using msvcrt."""
    import msvcrt

    sys.stdout.write(f"{prompt_str}: {default}")
    sys.stdout.flush()

    buf = list(default)

    while True:
        ch = msvcrt.getwch()

        # --- Enter ---
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(buf)

        # --- Backspace ---
        if ch == "\b":
            if buf:
                buf.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue

        # --- Ctrl-C ---
        if ch == "\x03":
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise KeyboardInterrupt

        # --- Ctrl-D / Ctrl-Z (EOF) ---
        if ch in ("\x04", "\x1a"):
            if not buf:
                sys.stdout.write("\n")
                sys.stdout.flush()
                raise EOFError
            continue

        # --- Special keys (arrows etc): prefix \xe0 or \x00 + 1 byte ---
        if ch in ("\xe0", "\x00"):
            msvcrt.getwch()  # consume and ignore the second byte
            continue

        # --- Printable ---
        if ch.isprintable():
            buf.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def prompt_text(label: str, default: str = "", allow_empty: bool = False) -> str:
    """Free-text prompt. If `default` is given, it's pre-filled at the cursor
    so the user can press Enter to accept or start typing/editing immediately."""
    while True:
        if default:
            raw = _editable_input(c_question(label), default).strip()
        else:
            raw = input(f"{c_question(label)}: ").strip()

        if raw or allow_empty:
            return raw
        print(c_error("Value cannot be empty."))


def prompt_int(label: str, default: int = None,
               min_val: int = None, max_val: int = None) -> int:
    while True:
        raw = prompt_text(label, str(default) if default is not None else "")
        if not raw and default is not None:
            return default
        try:
            n = int(raw)
        except ValueError:
            print(c_error(f"'{raw}' is not a valid integer."))
            continue
        if min_val is not None and n < min_val:
            print(c_error(f"Must be at least {min_val}."))
            continue
        if max_val is not None and n > max_val:
            print(c_error(f"Must be at most {max_val}."))
            continue
        return n


def prompt_yes_no(label: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{c_question(label)} {c_hint(suffix)}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(c_error("Please answer yes or no."))


def prompt_choice(label: str, options, default_idx: int = None) -> int:
    """Show numbered options, return the 1-based index chosen by the user."""
    print(c_question(label))
    for i, opt in enumerate(options, 1):
        marker = c_hint("  (default)") if (default_idx is not None and i == default_idx) else ""
        print(f"  {c_number(str(i))}) {opt}{marker}")
    while True:
        raw = input(f"  {c_question('Select')}: ").strip()
        if not raw and default_idx is not None:
            return default_idx
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return idx
        except ValueError:
            pass
        print(c_error(f"Please enter a number between 1 and {len(options)}."))


def warn_if_path_missing(path_str: str):
    p = Path(path_str)
    if not p.exists():
        print(c_warn(f"  Note: path '{path_str}' does not exist yet (this is OK if backups will arrive there later)."))
    elif not p.is_dir():
        print(c_warn(f"  Note: '{path_str}' exists but is not a directory."))


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

class Wizard:
    def __init__(self, output_path: str = None):
        self.output_path = output_path or "vcf-retention-config.json"
        self.config = {
            "log": {},
            "backup_targets": [],
        }

    # --- entry --------------------------------------------------------------

    def run(self):
        banner()
        print(c_hint(
            "This wizard will ask you a few questions and produce a JSON\n"
            "config file you can use with vcf_backup_retention.py.\n"
            "\n"
            "TIP: Where a default value is offered, it is pre-filled at the\n"
            "     cursor. Press Enter to accept, or use Backspace / typing\n"
            "     to edit it.\n"
        ))

        mode = prompt_choice(
            "Choose a setup mode:",
            [
                "Simple   - guided setup using built-in VCF presets (recommended)",
                "Advanced - full control: custom targets, regex patterns, overrides",
            ],
            default_idx=1,
        )

        if mode == 1:
            self.run_simple()
        else:
            self.run_advanced()

        self.preview_and_save()

    # --- simple mode --------------------------------------------------------

    def run_simple(self):
        section("Simple setup")

        # Which VCF version - explicit choice, no default. If the user has
        # both versions, they can run the wizard twice (once per version).
        vcf_choice = prompt_choice(
            "Which VCF version do you back up?",
            ["VCF 5.2.x only", "VCF 9.x only"],
        )
        do_v5 = (vcf_choice == 1)
        do_v9 = (vcf_choice == 2)

        # Base path
        base_root = prompt_text(
            "Where on this server do backups land? (base path)",
            default=default_backup_root(),
        )

        # Log
        section("Logging")
        log_file = prompt_text(
            "Log file path",
            default=default_log_path(),
        )
        self.config["log"] = {
            "file": log_file,
            "level": "INFO",
            "max_size_mb": 10,
            "backup_count": 5,
        }

        # Components per VCF
        if do_v5:
            section("VCF 5.2.x components")
            self._add_components_simple("vcf52", base_root)

        if do_v9:
            section("VCF 9.x components")
            self._add_components_simple("vcf9", base_root)

    def _add_components_simple(self, vcf_key: str, base_root: str):
        for preset_name, label in COMPONENTS[vcf_key]:
            if not prompt_yes_no(f"Manage {label}?", default=True):
                continue
            sub = DEFAULT_SUBPATH[preset_name]
            default_path = f"{base_root.rstrip('/')}/{vcf_key}/{sub}"
            path = prompt_text(f"  Backup path for {label}", default=default_path)
            warn_if_path_missing(path)

            defaults = DEFAULT_RETENTION[preset_name]
            keep_days = prompt_int(
                f"  Keep backups for how many days?",
                default=defaults["keep_days"], min_val=1,
            )
            keep_min = prompt_int(
                f"  Always keep at least how many newest backups (safety floor)?",
                default=defaults["keep_minimum"], min_val=1,
            )

            target = {
                "name":    f"{'VCF 5.2.x' if vcf_key == 'vcf52' else 'VCF 9.x'} - {label}",
                "enabled": True,
                "path":    path,
                "preset":  preset_name,
                "retention": {
                    "keep_days":    keep_days,
                    "keep_minimum": keep_min,
                },
            }
            self.config["backup_targets"].append(target)
            print(c_success(f"  + Added target '{target['name']}'"))
            print()

    # --- advanced mode ------------------------------------------------------

    def run_advanced(self):
        section("Advanced setup")

        # Logging
        section("Logging")
        log_file = prompt_text(
            "Log file path",
            default=default_log_path(),
        )
        log_level = prompt_choice(
            "Log level:",
            ["DEBUG", "INFO", "WARNING", "ERROR"],
            default_idx=2,
        )
        log_size = prompt_int("Max log file size in MB before rotation", default=10, min_val=1)
        log_count = prompt_int("How many rotated log files to keep", default=5, min_val=1)
        self.config["log"] = {
            "file":         log_file,
            "level":        ["DEBUG", "INFO", "WARNING", "ERROR"][log_level - 1],
            "max_size_mb":  log_size,
            "backup_count": log_count,
        }

        # Targets
        while True:
            section(f"Add backup target #{len(self.config['backup_targets']) + 1}")

            kind = prompt_choice(
                "What kind of target?",
                [
                    "Use a built-in preset (sddc_manager / nsx / vcenter / vcf9_fleet)",
                    "Custom (write your own pattern, file or directory mode)",
                    "Done adding targets",
                ],
                default_idx=1,
            )
            if kind == 3:
                if not self.config["backup_targets"]:
                    print(c_error("You must add at least one target before finishing."))
                    continue
                break
            elif kind == 1:
                self._add_preset_target_advanced()
            else:
                self._add_custom_target_advanced()

    def _add_preset_target_advanced(self):
        # Pick preset
        preset_keys = ["sddc_manager", "nsx", "vcenter", "vcf9_fleet", "generic_timestamp_dir"]
        preset_labels = [
            "sddc_manager           - SDDC Manager .tar.gz files (VCF 5.x and 9.x)",
            "nsx                    - NSX/NSX-T timestamped folders (VCF 5.x and 9.x)",
            "vcenter                - vCenter VAMI sn_... folders (VCF 5.x and 9.x)",
            "vcf9_fleet             - VCF 9 Fleet Manager / Identity Broker / Automation",
            "generic_timestamp_dir  - any directory whose name is a timestamp",
        ]
        idx = prompt_choice("Which preset?", preset_labels, default_idx=1)
        preset = preset_keys[idx - 1]

        name = prompt_text("Display name for this target", default=f"VCF backup - {preset}")
        path = prompt_text("Backup root path", default=f"{default_backup_root()}/{preset}")
        warn_if_path_missing(path)
        enabled = prompt_yes_no("Enabled?", default=True)
        min_age = prompt_int(
            "Skip backups younger than X minutes (protects in-flight uploads)",
            default=60, min_val=0,
        )

        defaults = DEFAULT_RETENTION.get(preset, {"keep_days": 14, "keep_minimum": 5})
        keep_days = prompt_int("Keep backups newer than X days (set 0 for none)",
                               default=defaults["keep_days"], min_val=0)
        keep_count = prompt_int("Also keep newest N backups (set 0 for none)",
                                default=0, min_val=0)
        keep_min = prompt_int("Safety floor: always keep at least N newest backups",
                              default=defaults["keep_minimum"], min_val=1)

        target = {
            "name":            name,
            "enabled":         enabled,
            "path":            path,
            "preset":          preset,
            "min_age_minutes": min_age,
            "retention": {
                "keep_minimum": keep_min,
            },
        }
        if keep_days > 0:
            target["retention"]["keep_days"] = keep_days
        if keep_count > 0:
            target["retention"]["keep_count"] = keep_count

        self.config["backup_targets"].append(target)
        print(c_success(f"  + Added target '{name}'"))
        print()

    def _add_custom_target_advanced(self):
        name = prompt_text("Display name for this target")
        path = prompt_text("Backup root path", default=default_backup_root())
        warn_if_path_missing(path)
        enabled = prompt_yes_no("Enabled?", default=True)

        type_idx = prompt_choice(
            "What kind of items are the backups?",
            [
                "file      - each backup is a single file (e.g. .tar.gz, .zip)",
                "directory - each backup is a folder containing files",
            ],
            default_idx=1,
        )
        item_type = "file" if type_idx == 1 else "directory"

        recursive = prompt_yes_no(
            "Search subdirectories recursively?",
            default=True,
        )

        if item_type == "file":
            pattern, formats = self._choose_file_regex()
        else:
            pattern, formats = self._choose_dir_regex()

        min_age = prompt_int(
            "Skip backups younger than X minutes (protects in-flight uploads)",
            default=60, min_val=0,
        )
        keep_days = prompt_int("Keep backups newer than X days (0 = none)",
                               default=14, min_val=0)
        keep_count = prompt_int("Also keep newest N backups (0 = none)",
                                default=0, min_val=0)
        keep_min = prompt_int("Safety floor: always keep at least N newest backups",
                              default=5, min_val=1)

        target = {
            "name":              name,
            "enabled":           enabled,
            "path":              path,
            "type":              item_type,
            "pattern":           pattern,
            "timestamp_formats": formats,
            "recursive":         recursive,
            "min_age_minutes":   min_age,
            "retention": {
                "keep_minimum": keep_min,
            },
        }
        if keep_days > 0:
            target["retention"]["keep_days"] = keep_days
        if keep_count > 0:
            target["retention"]["keep_count"] = keep_count

        self.config["backup_targets"].append(target)
        print(c_success(f"  + Added target '{name}'"))
        print()

    def _choose_file_regex(self):
        print()
        print(c_question("Pick a pattern for file names, or write your own:"))
        for i, ex in enumerate(FILE_REGEX_EXAMPLES, 1):
            print(f"  {c_number(str(i))}) {ex['label']}")
            if ex["pattern"]:
                print(f"     {c_hint('regex:')}    {c_value(ex['pattern'])}")
                if ex["formats"]:
                    print(f"     {c_hint('formats:')}  {c_value(', '.join(ex['formats']))}")
                else:
                    print(f"     {c_hint('formats:')}  {c_value('(empty - mtime fallback)')}")
                print(f"     {c_hint('matches:')}  {c_value(ex['matches'])}")
            print()

        idx = prompt_choice("Select", [str(i + 1) for i in range(len(FILE_REGEX_EXAMPLES))],
                            default_idx=1)
        ex = FILE_REGEX_EXAMPLES[idx - 1]

        if ex["pattern"] is not None:
            # Let user tweak the pattern if they want
            if prompt_yes_no("Tweak this pattern before saving?", default=False):
                pattern = prompt_text("Pattern (regex)", default=ex["pattern"])
                fmts_str = prompt_text(
                    "Timestamp formats (comma-separated, empty = mtime fallback)",
                    default=",".join(ex["formats"]),
                    allow_empty=True,
                )
                formats = [f.strip() for f in fmts_str.split(",") if f.strip()] if fmts_str else []
            else:
                pattern = ex["pattern"]
                formats = list(ex["formats"])
            self._validate_regex(pattern)
            return pattern, formats
        else:
            # Custom
            print(c_hint("Write your own regex. Reminder: it must match the file NAME only,"))
            print(c_hint("not the full path. In the regex, use capture groups around the date/"))
            print(c_hint("time portion(s); they will be concatenated and parsed."))
            pattern = prompt_text("Pattern (regex)")
            self._validate_regex(pattern)
            print(c_hint("Timestamp formats: comma-separated strptime formats to try."))
            print(c_hint("Examples: '%Y-%m-%d-%H-%M-%S', '%Y%m%d%H%M%S', '%Y-%m-%d'"))
            print(c_hint("Leave empty to use file's mtime as the age (less reliable)."))
            fmts_str = prompt_text("Timestamp formats", default="", allow_empty=True)
            formats = [f.strip() for f in fmts_str.split(",") if f.strip()] if fmts_str else []
            return pattern, formats

    def _choose_dir_regex(self):
        # Smaller catalog for directory mode - the presets cover most cases
        examples = [
            {
                "label":   "NSX-style timestamp dir like '2025-04-25-03-00-00'",
                "pattern": r"^\d{4}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}$",
                "formats": ["%Y-%m-%d-%H-%M-%S", "%Y_%m_%d_%H_%M_%S"],
            },
            {
                "label":   "ISO timestamp dir like '2025-04-25T03-00-00'",
                "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$",
                "formats": ["%Y-%m-%dT%H-%M-%S"],
            },
            {
                "label":   "Compact timestamp dir like '20250425-030000'",
                "pattern": r"^\d{8}-\d{6}$",
                "formats": ["%Y%m%d-%H%M%S"],
            },
            {
                "label":   "Custom - write your own pattern",
                "pattern": None,
                "formats": None,
            },
        ]
        print()
        print(c_question("Pick a pattern for directory names, or write your own:"))
        for i, ex in enumerate(examples, 1):
            print(f"  {c_number(str(i))}) {ex['label']}")
            if ex["pattern"]:
                print(f"     {c_hint('regex:')}    {c_value(ex['pattern'])}")
                print(f"     {c_hint('formats:')}  {c_value(', '.join(ex['formats']))}")
            print()

        idx = prompt_choice("Select", [str(i + 1) for i in range(len(examples))],
                            default_idx=1)
        ex = examples[idx - 1]

        if ex["pattern"] is not None:
            if prompt_yes_no("Tweak this pattern before saving?", default=False):
                pattern = prompt_text("Pattern (regex)", default=ex["pattern"])
                fmts_str = prompt_text("Timestamp formats (comma-separated)",
                                       default=",".join(ex["formats"]))
                formats = [f.strip() for f in fmts_str.split(",") if f.strip()]
            else:
                pattern, formats = ex["pattern"], list(ex["formats"])
            self._validate_regex(pattern)
            return pattern, formats
        else:
            pattern = prompt_text("Pattern (regex, must match directory name only)")
            self._validate_regex(pattern)
            fmts_str = prompt_text("Timestamp formats (comma-separated, empty = mtime)",
                                   default="", allow_empty=True)
            formats = [f.strip() for f in fmts_str.split(",") if f.strip()] if fmts_str else []
            return pattern, formats

    def _validate_regex(self, pattern: str):
        try:
            re.compile(pattern)
        except re.error as e:
            print(c_error(f"  Warning: regex did not compile: {e}"))
            print(c_warn("  The config will be saved anyway, but the script will fail on this target."))

    # --- output -------------------------------------------------------------

    def preview_and_save(self):
        section("Preview")

        if not self.config["backup_targets"]:
            print(c_error("No targets were configured - nothing to save."))
            sys.exit(1)

        rendered = json.dumps(self.config, indent=2)
        # Light syntax highlight: keys cyan, strings green, numbers yellow, bools magenta
        rendered = self._highlight_json(rendered)
        print(rendered)
        print()

        print(c_hint(
            f"Summary: {len(self.config['backup_targets'])} target(s), "
            f"{sum(1 for t in self.config['backup_targets'] if t.get('enabled', True))} enabled."
        ))

        out = prompt_text("Save to file", default=self.output_path)
        out_path = Path(out)
        if out_path.exists():
            if not prompt_yes_no(
                f"File '{out}' exists. Overwrite?", default=False,
            ):
                print(c_warn("Cancelled. Pick a different filename."))
                return self.preview_and_save()

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
                f.write("\n")
            print(c_success(f"\nConfig saved to: {out_path.resolve()}"))
        except OSError as e:
            print(c_error(f"\nFailed to save: {e}"))
            sys.exit(1)

        # Next steps
        print()
        print(c_header("Next steps:"))
        if IS_WINDOWS:
            cmd_dry = f"python vcf_backup_retention.py -c {out_path} --dry-run --verbose"
            cmd_live = f"python vcf_backup_retention.py -c {out_path}"
        else:
            cmd_dry = f"python3 vcf_backup_retention.py -c {out_path} --dry-run --verbose"
            cmd_live = f"python3 vcf_backup_retention.py -c {out_path}"
        print(f"  1. Test (no deletes):  {c_value(cmd_dry)}")
        print(f"  2. Run live:           {c_value(cmd_live)}")
        print(f"  3. Schedule via cron (Linux) or Task Scheduler (Windows).")
        print(f"     See README.md for details.")
        print()

    def _highlight_json(self, text: str) -> str:
        # Color JSON keys cyan, string values green, numbers yellow, true/false/null magenta.
        # Lightweight - operates on the already-formatted JSON.
        # Keys (followed by ':')
        text = re.sub(
            r'("(?:[^"\\]|\\.)*")(\s*:)',
            lambda m: f"{Fore.CYAN}{m.group(1)}{Style.RESET_ALL}{m.group(2)}",
            text,
        )
        # Remaining strings (already-replaced keys won't match because color codes are non-quote chars)
        text = re.sub(
            r'(?<![\x1b\w])"((?:[^"\\]|\\.)*)"',
            lambda m: f"{Fore.GREEN}\"{m.group(1)}\"{Style.RESET_ALL}",
            text,
        )
        # Numbers
        text = re.sub(
            r'(?<=[:\[,\s])(-?\d+(?:\.\d+)?)(?=[,\s\]\}])',
            lambda m: f"{Fore.YELLOW}{m.group(1)}{Style.RESET_ALL}",
            text,
        )
        # Booleans / null
        text = re.sub(
            r'\b(true|false|null)\b',
            lambda m: f"{Fore.MAGENTA}{m.group(1)}{Style.RESET_ALL}",
            text,
        )
        return text


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Interactive wizard generating a JSON config for vcf_backup_retention.py.",
    )
    parser.add_argument("-o", "--output", default="vcf-retention-config.json",
                        help="Default output file name (default: %(default)s)")
    args = parser.parse_args()

    try:
        Wizard(output_path=args.output).run()
    except (KeyboardInterrupt, EOFError):
        print()
        print(c_warn("Cancelled by user."))
        sys.exit(130)


if __name__ == "__main__":
    main()

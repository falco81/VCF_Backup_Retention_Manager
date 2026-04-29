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
        ("sddc_manager",   "SDDC Manager (.tar.gz files)"),
        ("nsx",            "NSX-T (timestamped folders)"),
        ("nsx_inventory",  "NSX inventory summaries (inventory-*.json)"),
        ("vcenter",        "vCenter Server (VAMI)"),
    ],
    "vcf9": [
        ("sddc_manager",   "SDDC Manager (.tar.gz files)"),
        ("nsx",            "NSX (timestamped folders)"),
        ("nsx_inventory",  "NSX inventory summaries (inventory-*.json)"),
        ("vcenter",        "vCenter Server (VAMI)"),
        ("vcf9_fleet",     "Fleet Mgmt (Fleet Manager + Identity Broker + Automation)"),
    ],
}

# Default retention values per preset (sensible starting points)
DEFAULT_RETENTION = {
    "sddc_manager":  {"keep_days": 30, "keep_minimum": 10},
    "nsx":           {"keep_days": 14, "keep_minimum": 7},
    "nsx_inventory": {"keep_days": 14, "keep_minimum": 7},
    "vcenter":       {"keep_days": 30, "keep_minimum": 7},
    "vcf9_fleet":    {"keep_days": 14, "keep_minimum": 5},
}

# Default folder name suggestions per preset
DEFAULT_SUBPATH = {
    "sddc_manager":  "sddc-manager-backup",
    "nsx":           "cluster-node-backups",
    "nsx_inventory": "inventory-summary",
    "vcenter":       "vCenter",
    "vcf9_fleet":    "fleet",
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
# Autodetection
# ---------------------------------------------------------------------------
# Walk a top-level directory and try to identify VCF instances + which
# components each instance holds. Detection works by checking whether the
# preset's regex matches anything inside a candidate directory.

import re as _re

# Subfolder names commonly used by VCF for each component. The detector
# checks both these conventional names and falls back to recursive scan.
COMPONENT_HINTS = {
    "sddc_manager":  ["sddc-manager-backup", "sddc-manager"],
    "nsx":           ["cluster-node-backups", "nsx", "nsx-t"],
    "nsx_inventory": ["inventory-summary"],
    "vcenter":       ["vCenter", "vcenter", "vc"],
    "vcf9_fleet":    ["fleet", "fleet-backups"],
}


def _scan_for_preset(path: Path, preset_key: str, max_depth: int = 4) -> bool:
    """Return True if at least one item under `path` matches `preset_key`."""
    from vcf_backup_retention import PRESETS  # type: ignore
    preset = PRESETS.get(preset_key)
    if preset is None:
        return False
    try:
        regex = _re.compile(preset["pattern"])
    except _re.error:
        return False
    want_file = (preset["type"] == "file")

    # BFS up to max_depth levels (avoid pathologically deep scans)
    stack = [(path, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            for item in current.iterdir():
                try:
                    if regex.match(item.name):
                        if want_file and item.is_file():
                            return True
                        if (not want_file) and item.is_dir():
                            return True
                    if item.is_dir() and depth < max_depth:
                        stack.append((item, depth + 1))
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError, NotADirectoryError):
            continue
    return False


def _resolve_component_path(instance_dir: Path, preset_key: str) -> Path:
    """Pick the most likely path within an instance for a given component.

    First check conventional subfolder names; if a hint matches, return it.
    Otherwise return the instance_dir itself (the script searches recursively
    so it'll still find the items)."""
    hints = COMPONENT_HINTS.get(preset_key, [])
    for h in hints:
        candidate = instance_dir / h
        if candidate.is_dir():
            return candidate
    return instance_dir


def _detect_instances(top: Path):
    """List subdirectories of `top` that look like backup instances.

    Returns a list of Path objects. Instances are immediate subdirs that
    are not hidden and contain at least one matching component.
    """
    detected, _skipped = _detect_instances_with_skipped(top)
    return detected


def _detect_instances_with_skipped(top: Path):
    """Like _detect_instances but also returns the list of subdirs that
    were checked but had no recognised content. Useful for letting users
    know about empty/newly-set-up directories.

    Returns (detected, skipped) where each is a list of Path objects.
    """
    if not top.exists() or not top.is_dir():
        return [], []
    detected = []
    skipped = []
    try:
        for entry in sorted(top.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            # Quick check: does ANY preset find anything here?
            found = False
            for preset_key in COMPONENT_HINTS.keys():
                if _scan_for_preset(entry, preset_key, max_depth=4):
                    detected.append(entry)
                    found = True
                    break
            if not found:
                skipped.append(entry)
    except (OSError, PermissionError):
        pass
    return detected, skipped


def _detect_components(instance_dir: Path):
    """Return list of (preset_key, resolved_path) tuples for components
    found inside an instance directory."""
    found = []
    for preset_key in ["sddc_manager", "nsx", "nsx_inventory", "vcenter", "vcf9_fleet"]:
        path = _resolve_component_path(instance_dir, preset_key)
        if _scan_for_preset(path, preset_key, max_depth=4):
            found.append((preset_key, path))
    return found


def _guess_vcf_version(component_keys) -> str:
    """Return 'vcf52' or 'vcf9' based on detected components."""
    if "vcf9_fleet" in component_keys:
        return "vcf9"
    return "vcf52"


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
            "\n"
            "TIP: You can manage multiple VCF instances in one config. The\n"
            "     wizard will let you add instances one after another, or\n"
            "     load an existing config and append to it later.\n"
        ))

        mode = prompt_choice(
            "Choose a setup mode:",
            [
                "Simple    - guided setup using built-in VCF presets (recommended)",
                "Advanced  - full control: custom targets, regex patterns, overrides",
                "Autodetect VCF 5.2.x - point at a top folder, wizard finds instances",
                "Autodetect VCF 9.x   - point at a top folder, wizard finds instances",
            ],
            default_idx=1,
        )

        if mode == 1:
            self.run_simple()
        elif mode == 2:
            self.run_advanced()
        elif mode == 3:
            self.run_autodetect(vcf_hint="vcf52")
        else:
            self.run_autodetect(vcf_hint="vcf9")

        self.preview_and_save()

    # --- simple mode --------------------------------------------------------

    def run_simple(self):
        section("Simple setup")

        # Optionally start from an existing config (so users can append more
        # instances to a config they built earlier).
        self._maybe_load_existing_config()

        # If we just loaded an existing config, log already exists; otherwise
        # ask now so it's set before any targets are added.
        if not self.config["log"]:
            self._ask_log_settings_simple()

        # Loop: one instance at a time. Each instance is one VCF deployment
        # with its own SFTP root (e.g. /home/backup/b-vcf, /home/backup/c-vcf).
        instance_idx = 0
        while True:
            instance_idx += 1
            existing = len(self.config["backup_targets"])
            if existing > 0 and instance_idx == 1:
                print(c_hint(
                    f"Loaded existing config has {existing} target(s). "
                    f"You can now add more instances to it.\n"
                ))

            section(f"Instance #{instance_idx}")

            # VCF version for this instance
            vcf_choice = prompt_choice(
                "Which VCF version is this instance?",
                ["VCF 5.2.x", "VCF 9.x"],
            )
            vcf_key = "vcf52" if vcf_choice == 1 else "vcf9"

            # Instance label - used as a prefix in target names so the user
            # can tell instances apart later in logs.
            instance_label = prompt_text(
                "Instance label (used as prefix in target names, e.g. 'b-vcf', 'prod', 'site-a')",
            )

            # Backup root for this instance
            base_root = prompt_text(
                "SFTP root path for this instance",
                default=f"/home/backup/{instance_label}",
            )

            # Components for this instance
            print()
            print(c_hint(f"Now selecting components for instance '{instance_label}'..."))
            self._add_components_simple(vcf_key, base_root, instance_label)

            # Add another instance?
            print()
            if not prompt_yes_no(
                f"Add another VCF instance to this config?",
                default=False,
            ):
                break

    def _ask_log_settings_simple(self):
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

    # --- autodetect mode ----------------------------------------------------

    def run_autodetect(self, vcf_hint: str):
        """Walk a top-level folder, detect instances + components, and let
        the user accept or skip each one. `vcf_hint` is 'vcf52' or 'vcf9' -
        used when component-based detection is ambiguous."""
        section(f"Autodetect ({'VCF 5.2.x' if vcf_hint == 'vcf52' else 'VCF 9.x'})")

        # Optionally start from existing config
        self._maybe_load_existing_config()

        if not self.config["log"]:
            self._ask_log_settings_simple()

        top = prompt_text(
            "Top-level folder to scan for instances",
            default="/home/backup",
        )
        top_path = Path(top)
        if not top_path.exists() or not top_path.is_dir():
            print(c_error(f"  '{top}' does not exist or is not a directory."))
            return

        # Build a set of (path, preset) tuples that are ALREADY in the config
        # so we can skip them in autodetect rather than duplicating.
        existing = {
            (str(Path(t["path"]).resolve()), t.get("preset"))
            for t in self.config["backup_targets"]
            if t.get("preset")
        }

        print()
        print(c_hint(f"Scanning {top_path} ..."))
        instances, empty_subdirs = _detect_instances_with_skipped(top_path)

        # Report empty / non-VCF subdirs - useful info for the user
        if empty_subdirs:
            print(c_warn(f"  Skipped {len(empty_subdirs)} subdir(s) with no recognised backups:"))
            for sd in empty_subdirs:
                print(f"    - {sd.name}  {c_hint('(empty or no recognised content - maybe newly set up?)')}")
            print()

        if not instances:
            print(c_error("  No VCF backup instances found in this folder."))
            print(c_hint("  Tip: an instance is a subdirectory that contains at least one"))
            print(c_hint("       recognised VCF backup (SDDC Manager .tar.gz, NSX backup-*UTC,"))
            print(c_hint("       vCenter sn_*/M_*, etc)."))
            return

        # Show summary first
        print(c_success(f"  Found {len(instances)} candidate instance(s):"))
        plan = []  # list of dicts: {instance_dir, label, vcf_key, components, new_components}
        for inst in instances:
            comps = _detect_components(inst)
            if not comps:
                continue
            comp_keys = [k for k, _ in comps]
            detected_version = _guess_vcf_version(comp_keys)

            # Filter out components that are already in the existing config
            new_comps = [
                (k, p) for k, p in comps
                if (str(p.resolve()), k) not in existing
            ]
            already_in_config = len(comps) - len(new_comps)

            plan.append({
                "instance_dir":   inst,
                "label":          inst.name,
                "vcf_key":        detected_version,
                "components":     new_comps,
                "all_components": comps,  # for display only
                "already_count":  already_in_config,
            })
            v_label = "VCF 9.x" if detected_version == "vcf9" else "VCF 5.2.x"
            comp_summary = ", ".join(k for k, _ in comps)
            already_note = c_hint(f"  [{already_in_config} already in config]") if already_in_config > 0 else ""
            print(f"    {c_value(inst.name):40s}  {c_hint(v_label)}  ({comp_summary}){already_note}")

        if not plan:
            print(c_error("  None of the detected instances had recognisable components."))
            return

        # Anything new to add?
        total_new = sum(len(e["components"]) for e in plan)
        if total_new == 0:
            print()
            print(c_success(
                "  All detected components are already in the loaded config - nothing to add."
            ))
            return

        print()
        print(c_hint(
            "For each instance below, the wizard will show what it found and\n"
            "ask you to confirm. Press Enter to accept all defaults, or 'n'\n"
            "to skip an instance / component. Components already in the loaded\n"
            "config are skipped automatically."
        ))

        # Per-instance confirmation
        for entry in plan:
            if not entry["components"]:
                # All components for this instance were already in the config
                continue
            self._autodetect_confirm_instance(entry, vcf_hint)

    def _autodetect_confirm_instance(self, entry: dict, vcf_hint: str):
        inst = entry["instance_dir"]
        detected_version = entry["vcf_key"]
        components = entry["components"]

        section(f"Instance: {entry['label']}")

        # If detection differs from the user's hint, warn but trust detection
        if detected_version != vcf_hint:
            actual = "VCF 9.x" if detected_version == "vcf9" else "VCF 5.2.x"
            hint_label = "VCF 9.x" if vcf_hint == "vcf9" else "VCF 5.2.x"
            print(c_warn(
                f"  Note: detected components suggest {actual}, "
                f"but you chose autodetect mode for {hint_label}."
            ))

        # Confirm we want this instance at all
        if not prompt_yes_no(f"Include instance '{entry['label']}' in config?", default=True):
            print(c_hint(f"  Skipping {entry['label']}."))
            return

        # Confirm/edit instance label (used as prefix in target names)
        instance_label = prompt_text("Instance label (prefix in target names)", default=entry["label"])

        # Per-component confirmation
        version_label = "VCF 9.x" if detected_version == "vcf9" else "VCF 5.2.x"
        for preset_key, detected_path in components:
            display_name = next(
                (n for k, n in COMPONENTS[detected_version] if k == preset_key),
                preset_key,
            )

            print()
            print(f"  Component: {c_value(display_name)}")
            print(f"  {c_hint('Detected at:')} {c_value(str(detected_path))}")

            if not prompt_yes_no(f"  Include this component?", default=True):
                continue

            path = prompt_text(f"  Path", default=str(detected_path))
            defaults = DEFAULT_RETENTION[preset_key]
            keep_days = prompt_int(
                f"  Keep backups for how many days?",
                default=defaults["keep_days"], min_val=1,
            )
            keep_min = prompt_int(
                f"  Always keep at least how many newest backups?",
                default=defaults["keep_minimum"], min_val=1,
            )

            target = {
                "name":    f"[{instance_label}] {version_label} - {display_name}",
                "enabled": True,
                "path":    path,
                "preset":  preset_key,
                "retention": {
                    "keep_days":    keep_days,
                    "keep_minimum": keep_min,
                },
            }
            self.config["backup_targets"].append(target)
            print(c_success(f"    + Added '{target['name']}'"))

    def _maybe_load_existing_config(self):
        """Optionally load an existing config to append to."""
        if not prompt_yes_no(
            "Start from an existing config (to add more instances to it)?",
            default=False,
        ):
            return

        existing_path = prompt_text(
            "Path to existing config",
            default=self.output_path,
        )
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if "backup_targets" not in loaded:
                print(c_error("File does not look like a valid retention config (no 'backup_targets')."))
                return
            self.config = {
                "log": loaded.get("log", {}),
                "backup_targets": loaded.get("backup_targets", []),
            }
            self.output_path = existing_path  # save back to same file by default
            print(c_success(
                f"Loaded {len(self.config['backup_targets'])} existing target(s) from {existing_path}"
            ))
        except FileNotFoundError:
            print(c_warn(f"File not found - starting fresh."))
        except json.JSONDecodeError as e:
            print(c_error(f"Invalid JSON in {existing_path}: {e}. Starting fresh."))

    def _add_components_simple(self, vcf_key: str, base_root: str, instance_label: str = ""):
        version_label = "VCF 5.2.x" if vcf_key == "vcf52" else "VCF 9.x"
        for preset_name, label in COMPONENTS[vcf_key]:
            if not prompt_yes_no(f"Manage {label}?", default=True):
                continue
            sub = DEFAULT_SUBPATH[preset_name]
            default_path = f"{base_root.rstrip('/')}/{sub}"
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

            # Compose name with optional instance label prefix
            if instance_label:
                target_name = f"[{instance_label}] {version_label} - {label}"
            else:
                target_name = f"{version_label} - {label}"

            target = {
                "name":    target_name,
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

        # Optionally start from an existing config (so users can append more
        # targets to a config they built earlier).
        self._maybe_load_existing_config()

        # Logging - only ask if not loaded from existing config
        if not self.config["log"]:
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
        else:
            existing = len(self.config["backup_targets"])
            if existing > 0:
                print(c_hint(
                    f"Loaded existing config has {existing} target(s). "
                    f"Add more below.\n"
                ))

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
        preset_keys = ["sddc_manager", "nsx", "nsx_inventory", "vcenter", "vcf9_fleet", "generic_timestamp_dir"]
        preset_labels = [
            "sddc_manager           - SDDC Manager .tar.gz + .sha256 files (VCF 5.x and 9.x)",
            "nsx                    - NSX/NSX-T backup-*UTC folders (VCF 5.x and 9.x)",
            "nsx_inventory          - NSX inventory-*UTC.json files (VCF 5.x and 9.x)",
            "vcenter                - vCenter VAMI M_..._<date>-<time>_/ folders",
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

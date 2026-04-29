#!/usr/bin/env python3
"""
VCF Backup Retention Manager
============================
Manages retention of VMware Cloud Foundation backups (SDDC Manager, NSX,
vCenter, Fleet Manager / Identity Broker / Automation) stored on a Linux
or Windows server via SCP/SFTP/SMB.

Cross-platform: works on Linux and Windows 10+. Pure Python 3.8+, no
external dependencies (uses only the standard library).

Usage:
    python vcf_backup_retention.py -c config.json
    python vcf_backup_retention.py -c config.json --dry-run
    python vcf_backup_retention.py -c config.json --verbose

Cron (Linux):
    15 3 * * * /usr/bin/python3 /opt/vcf-retention/vcf_backup_retention.py \\
               -c /opt/vcf-retention/config.json >/dev/null 2>&1

Task Scheduler (Windows): see README.md.
"""

import argparse
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ----------------------------------------------------------------------------
# Presets for known VCF / VMware backup formats
# ----------------------------------------------------------------------------
# Each preset defines:
#   type:               "directory" or "file"
#   pattern:            regex applied to the file/directory NAME (not full path)
#                       Optional capture groups are concatenated and parsed as
#                       the timestamp. If there are no groups, the whole name
#                       is parsed as timestamp.
#   timestamp_formats:  list of strptime formats tried in order
#   recursive:          whether to descend into subdirectories (default True)
# ----------------------------------------------------------------------------

PRESETS = {
    # NSX / NSX-T over SFTP. SDDC Manager configures NSX backup automatically.
    # Real VCF 5.2.x format observed on disk:
    #   <root>/<node-uuid>/backup-2026-04-29T10_52_44UTC/
    # Older / VCF 4.x style:
    #   <root>/cluster-node-backups/<node-uuid>/2025-04-25-03-00-00/
    # Pattern matches both styles. Outer alternation is non-capturing so
    # only the timestamp itself is extracted by capture groups.
    "nsx": {
        "type": "directory",
        "pattern": (
            r"^(?:"
            r"backup-(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})UTC"      # VCF 5.x: backup-2026-04-29T10_52_44UTC
            r"|backup-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})UTC"     # variant with dashes
            r"|(\d{4}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2})"  # legacy: 2025-04-25-03-00-00
            r")$"
        ),
        "timestamp_formats": [
            "%Y-%m-%dT%H_%M_%S",
            "%Y-%m-%dT%H-%M-%S",
            "%Y-%m-%d-%H-%M-%S",
            "%Y_%m_%d_%H_%M_%S",
        ],
        "recursive": True,
    },

    # NSX inventory summaries - emitted alongside NSX backups in VCF 5.x.
    # Files like: inventory-2026-04-29T10_57_04UTC.json
    "nsx_inventory": {
        "type": "file",
        "pattern": r"^inventory-(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})UTC\.json$",
        "timestamp_formats": ["%Y-%m-%dT%H_%M_%S"],
        "recursive": True,
    },

    # SDDC Manager file-based backup.
    # Naming: vcf-backup-<hostname>-<domain-with-dashes>-<YYYY-MM-DD-HH-MM-SS>.tar.gz
    # The matching .sha256 sidecar file is also covered. Outer alternation
    # for the extension is non-capturing so only the timestamp is captured.
    "sddc_manager": {
        "type": "file",
        "pattern": r"^vcf-backup-.+-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.(?:tar\.gz|sha256)$",
        "timestamp_formats": ["%Y-%m-%d-%H-%M-%S"],
        "recursive": True,
    },

    # vCenter Server file-based backup (VAMI). Layout observed on real VCF
    # 5.2.x systems is two levels:
    #   <root>/sn_<fqdn>/M_<version>_<YYYYMMDD>-<HHMMSS>_/<files>
    # The timestamp folder we manage is the inner 'M_..._..._' one. Older
    # vCenter releases stored a flat folder name with everything baked in:
    #   sn_<ip>M_<version>_<YYYYMMDD>_<HHMMSS>_<base64>=
    # Both forms are matched. Outer alternation is non-capturing.
    "vcenter": {
        "type": "directory",
        "pattern": (
            r"^(?:"
            r"M_.+?_(\d{8})-(\d{6})_?"          # nested: M_8.0.3.00800_20260429-104119_
            r"|sn_.+?_(\d{8})_(\d{6})_.+"       # legacy flat: sn_<ip>M_..._20250425_030015_<hash>=
            r")$"
        ),
        "timestamp_formats": ["%Y%m%d%H%M%S"],
        "recursive": True,
    },

    # VCF 9 Fleet Management backups (Fleet Manager, VCF Identity Broker,
    # VCF Automation). Layout (per Broadcom docs):
    #   vcf/backups/<cluster-name>/<version>/<component-name>/<timestamp>/<file>.tgz
    # The timestamp folder is what we manage. Outer alternation non-capturing.
    "vcf9_fleet": {
        "type": "directory",
        "pattern": (
            r"^(?:"
            r"\d{4}-\d{2}-\d{2}[-T_]\d{2}[-:_]\d{2}[-:_]\d{2}(?:UTC|Z)?"  # ISO-ish
            r"|\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}"                       # NSX-style
            r"|\d{8}[-T_]\d{6}"                                           # compact
            r")$"
        ),
        "timestamp_formats": [
            "%Y-%m-%d-%H-%M-%S",
            "%Y-%m-%dT%H-%M-%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H_%M_%S",
            "%Y-%m-%d_%H-%M-%S",
            "%Y%m%d-%H%M%S",
            "%Y%m%dT%H%M%S",
            "%Y%m%d_%H%M%S",
        ],
        "recursive": True,
    },

    # Generic catch-all for any timestamped directory.
    "generic_timestamp_dir": {
        "type": "directory",
        "pattern": (
            r"^(?:"
            r"\d{4}-\d{2}-\d{2}[-T_]\d{2}[-:_]\d{2}[-:_]\d{2}(?:UTC|Z)?"
            r"|\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}"
            r"|\d{8}[-T_]\d{6}"
            r")$"
        ),
        "timestamp_formats": [
            "%Y-%m-%d-%H-%M-%S",
            "%Y-%m-%dT%H-%M-%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H_%M_%S",
            "%Y%m%d-%H%M%S",
            "%Y%m%dT%H%M%S",
            "%Y%m%d_%H%M%S",
        ],
        "recursive": True,
    },
}

# ----------------------------------------------------------------------------
# Forbidden roots: refuse to delete anything sitting directly at these paths
# ----------------------------------------------------------------------------

_LINUX_FORBIDDEN = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
    "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin",
    "/srv", "/sys", "/tmp", "/usr", "/var",
}
_WINDOWS_FORBIDDEN = {
    "C:\\", "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\Users", "C:\\ProgramData",
    "D:\\", "E:\\", "F:\\",  # plain drive roots; backups should be in subfolders
}


def _is_windows() -> bool:
    return os.name == "nt"


def _forbidden_roots():
    return _WINDOWS_FORBIDDEN if _is_windows() else _LINUX_FORBIDDEN


def _default_log_path() -> str:
    """Return a sensible default log path for the current OS."""
    if _is_windows():
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return str(Path(base) / "vcf-retention" / "vcf-backup-retention.log")
    return "/var/log/vcf-backup-retention.log"


# ----------------------------------------------------------------------------

class BackupRetentionManager:
    def __init__(self, config_path: str, dry_run: bool = False, verbose: bool = False):
        self.config_path = config_path
        self.dry_run = dry_run
        self.verbose = verbose
        self.config = self._load_config(config_path)
        self.logger = self._setup_logging()
        self.stats = {
            "targets": 0,
            "skipped": 0,
            "scanned": 0,
            "kept": 0,
            "deleted": 0,
            "errors": 0,
            "bytes_freed": 0,
        }

    # --- bootstrap ----------------------------------------------------------

    def _load_config(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            sys.stderr.write(f"ERROR: Configuration file not found: {path}\n")
            sys.exit(2)
        except json.JSONDecodeError as e:
            sys.stderr.write(
                f"ERROR: Invalid JSON in {path} "
                f"(line {e.lineno}, column {e.colno}): {e.msg}\n"
            )
            sys.exit(2)

        if not isinstance(cfg, dict) or "backup_targets" not in cfg:
            sys.stderr.write(
                "ERROR: Config must be a JSON object with a 'backup_targets' key.\n"
            )
            sys.exit(2)
        return cfg

    def _setup_logging(self) -> logging.Logger:
        log_cfg = self.config.get("log", {}) or {}
        log_file = log_cfg.get("file") or _default_log_path()
        level_name = log_cfg.get("level", "INFO").upper()
        if self.verbose:
            level_name = "DEBUG"
        log_level = getattr(logging, level_name, logging.INFO)
        max_bytes = int(log_cfg.get("max_size_mb", 10)) * 1024 * 1024
        backup_count = int(log_cfg.get("backup_count", 5))

        logger = logging.getLogger("vcf_backup_retention")
        logger.setLevel(log_level)
        # Avoid duplicate handlers on repeated instantiation
        for h in list(logger.handlers):
            logger.removeHandler(h)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Rotating file handler
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except (PermissionError, OSError) as e:
            sys.stderr.write(
                f"WARN: Cannot write to log file {log_file}: {e}. "
                f"Falling back to stdout only.\n"
            )

        # Console handler (stdout)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        return logger

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_target_settings(target: dict) -> dict:
        """
        Combine preset values (if any) with per-target overrides.
        Per-target keys take precedence over preset keys.
        """
        merged: dict = {}
        preset_name = target.get("preset")
        if preset_name:
            preset = PRESETS.get(preset_name)
            if preset is None:
                raise ValueError(
                    f"Unknown preset '{preset_name}'. "
                    f"Available presets: {sorted(PRESETS.keys())}"
                )
            merged.update(preset)
        # Overrides on the target
        for key in ("type", "pattern", "timestamp_formats", "recursive"):
            if key in target:
                merged[key] = target[key]
        # Defaults
        merged.setdefault("type", "directory")
        merged.setdefault("pattern", PRESETS["generic_timestamp_dir"]["pattern"])
        merged.setdefault(
            "timestamp_formats",
            PRESETS["generic_timestamp_dir"]["timestamp_formats"],
        )
        merged.setdefault("recursive", True)
        return merged

    @staticmethod
    def _parse_timestamp_from_match(name: str, regex: re.Pattern, formats):
        """
        Try to extract and parse a timestamp from a name.

        - If the regex has capture groups, concatenate them (in order) and parse.
        - If the regex has no groups, parse the whole name.
        Returns a datetime or None.
        """
        m = regex.match(name)
        if not m:
            return None
        if m.groups():
            ts_str = "".join(g for g in m.groups() if g is not None)
        else:
            ts_str = name
        for fmt in formats:
            try:
                return datetime.strptime(ts_str, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _get_size(path: Path, is_file: bool) -> int:
        try:
            if is_file:
                return path.stat().st_size
            total = 0
            for p in path.rglob("*"):
                try:
                    if p.is_file() and not p.is_symlink():
                        total += p.stat().st_size
                except (OSError, PermissionError):
                    pass
            return total
        except (OSError, PermissionError):
            return 0

    def _is_path_safe(self, path: Path, root: Path) -> bool:
        try:
            real_path = path.resolve(strict=False)
            real_root = root.resolve(strict=False)
        except Exception:
            return False

        # Must not be the root itself
        if real_path == real_root:
            return False
        # Must be inside the configured root
        try:
            real_path.relative_to(real_root)
        except ValueError:
            return False
        # Must not be a system path
        if str(real_path) in _forbidden_roots():
            return False
        # Must have at least 3 parts (e.g. /backup/nsx/2025-... or C:\backup\nsx\...)
        if len(real_path.parts) < 3:
            return False
        return True

    def _find_items(self, root: Path, regex: re.Pattern, item_type: str,
                    recursive: bool):
        """Find files or directories whose name matches the regex."""
        results = []
        want_file = (item_type == "file")
        try:
            iterator = root.rglob("*") if recursive else root.iterdir()
            for item in iterator:
                try:
                    name = item.name
                    if not regex.match(name):
                        continue
                    if want_file and item.is_file():
                        results.append(item)
                    elif (not want_file) and item.is_dir():
                        results.append(item)
                except (OSError, PermissionError) as e:
                    self.logger.debug(f"Cannot access {item}: {e}")
        except (OSError, PermissionError) as e:
            self.logger.error(f"Cannot traverse {root}: {e}")
        return results

    def _get_age(self, item: Path, regex: re.Pattern, formats) -> datetime:
        ts = self._parse_timestamp_from_match(item.name, regex, formats)
        if ts is not None:
            return ts
        try:
            return datetime.fromtimestamp(item.stat().st_mtime)
        except OSError:
            return datetime.now()

    # --- core logic ---------------------------------------------------------

    def process_target(self, target: dict):
        name = target.get("name", target.get("path", "?"))

        # Allow targets to be temporarily disabled without removing them.
        # Default is True for backward compatibility.
        if not target.get("enabled", True):
            self.logger.info(f"--- Skipping disabled target: {name} ---")
            self.stats["skipped"] += 1
            return

        path_str = target.get("path")
        if not path_str:
            self.logger.error(f"Target '{name}' has no 'path' set - skipping.")
            self.stats["errors"] += 1
            return

        try:
            settings = self._resolve_target_settings(target)
        except ValueError as e:
            self.logger.error(f"Target '{name}': {e}")
            self.stats["errors"] += 1
            return

        path = Path(path_str)
        item_type = settings["type"]
        pattern = settings["pattern"]
        formats = settings["timestamp_formats"]
        recursive = bool(settings["recursive"])

        retention = target.get("retention", {}) or {}
        keep_days = retention.get("keep_days")
        keep_count = retention.get("keep_count")
        keep_minimum = int(retention.get("keep_minimum", 1))
        min_age_minutes = int(target.get("min_age_minutes", 60))

        self.logger.info(f"=== Processing target: {name} ===")
        self.logger.info(f"  Path:           {path}")
        self.logger.info(f"  Type:           {item_type}")
        self.logger.info(f"  Pattern:        {pattern}")
        self.logger.info(f"  Recursive:      {recursive}")
        self.logger.info(f"  keep_days:      {keep_days}")
        self.logger.info(f"  keep_count:     {keep_count}")
        self.logger.info(f"  keep_minimum:   {keep_minimum}")
        self.logger.info(f"  min_age_min:    {min_age_minutes}")

        if keep_days is None and keep_count is None:
            self.logger.warning(
                "  Neither 'keep_days' nor 'keep_count' set - nothing will be deleted."
            )
            return

        if not path.exists():
            self.logger.error(f"  Path does not exist: {path}")
            self.stats["errors"] += 1
            return

        if not path.is_dir():
            self.logger.error(f"  Path is not a directory: {path}")
            self.stats["errors"] += 1
            return

        try:
            regex = re.compile(pattern)
        except re.error as e:
            self.logger.error(f"  Invalid regex '{pattern}': {e}")
            self.stats["errors"] += 1
            return

        self.stats["targets"] += 1

        items = self._find_items(path, regex, item_type, recursive)
        self.logger.info(f"  Found {len(items)} {item_type}(s) matching pattern")

        # Group by parent so retention is applied per logical bucket
        # (per NSX node, per cluster backup folder, per component, etc.)
        groups: dict = {}
        for it in items:
            groups.setdefault(it.parent, []).append(it)

        for parent in sorted(groups.keys()):
            self._apply_retention(
                parent_dir=parent,
                root=path,
                items=groups[parent],
                item_type=item_type,
                regex=regex,
                formats=formats,
                keep_days=keep_days,
                keep_count=keep_count,
                keep_minimum=keep_minimum,
                min_age_minutes=min_age_minutes,
            )

    def _apply_retention(self, parent_dir, root, items, item_type, regex, formats,
                         keep_days, keep_count, keep_minimum, min_age_minutes):
        # Pair (item, age), newest first
        with_age = [(it, self._get_age(it, regex, formats)) for it in items]
        with_age.sort(key=lambda x: x[1], reverse=True)

        try:
            rel = parent_dir.relative_to(root)
            label = str(rel) if str(rel) != "." else "<root>"
        except ValueError:
            label = str(parent_dir)

        self.logger.info(f"  Group '{label}': {len(with_age)} backups")
        self.stats["scanned"] += len(with_age)

        now = datetime.now()
        min_age_threshold = now - timedelta(minutes=min_age_minutes)

        to_delete = []

        for idx, (item, age) in enumerate(with_age):
            # 1) Always keep the newest 'keep_minimum'
            if idx < keep_minimum:
                self.logger.debug(f"    KEEP [{idx + 1}]: {item.name}  (minimum)")
                self.stats["kept"] += 1
                continue

            # 2) Never touch backups newer than min_age_minutes
            if age > min_age_threshold:
                self.logger.debug(
                    f"    KEEP [{idx + 1}]: {item.name}  "
                    f"(younger than {min_age_minutes} min)"
                )
                self.stats["kept"] += 1
                continue

            # 3) Within keep_count
            if keep_count is not None and idx < keep_count:
                self.logger.debug(
                    f"    KEEP [{idx + 1}]: {item.name}  (within keep_count={keep_count})"
                )
                self.stats["kept"] += 1
                continue

            # 4) Within keep_days
            if keep_days is not None:
                cutoff = now - timedelta(days=keep_days)
                if age >= cutoff:
                    self.logger.debug(
                        f"    KEEP [{idx + 1}]: {item.name}  "
                        f"(within keep_days={keep_days})"
                    )
                    self.stats["kept"] += 1
                    continue
                reason = f"older than {keep_days} days (age: {age:%Y-%m-%d %H:%M})"
            elif keep_count is not None:
                reason = f"beyond keep_count={keep_count}"
            else:
                self.stats["kept"] += 1
                continue

            to_delete.append((item, age, reason))

        for item, age, reason in to_delete:
            self._delete(item, root, item_type, age, reason)

    def _delete(self, path: Path, root: Path, item_type: str,
                age: datetime, reason: str):
        if not self._is_path_safe(path, root):
            self.logger.error(
                f"    SAFETY REFUSED: {path} - failed sanity check"
            )
            self.stats["errors"] += 1
            return

        size = self._get_size(path, is_file=(item_type == "file"))
        size_mb = size / (1024 * 1024)
        age_str = age.strftime("%Y-%m-%d %H:%M:%S")

        if self.dry_run:
            self.logger.info(
                f"    [DRY-RUN] Would delete: {path}  "
                f"(age: {age_str}, size: {size_mb:.1f} MB, reason: {reason})"
            )
            self.stats["deleted"] += 1
            self.stats["bytes_freed"] += size
            return

        try:
            if item_type == "file":
                path.unlink()
            else:
                shutil.rmtree(path)
            self.logger.info(
                f"    DELETED: {path}  "
                f"(age: {age_str}, size: {size_mb:.1f} MB, reason: {reason})"
            )
            self.stats["deleted"] += 1
            self.stats["bytes_freed"] += size
        except Exception as e:
            self.logger.error(f"    Failed to delete {path}: {e}")
            self.stats["errors"] += 1

    # --- entry point --------------------------------------------------------

    def run(self) -> int:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        self.logger.info("########################################################")
        self.logger.info(f"  VCF Backup Retention - start ({mode})")
        self.logger.info(f"  Config:   {self.config_path}")
        self.logger.info(f"  Hostname: {platform.node()}")
        self.logger.info(f"  System:   {platform.system()} {platform.release()}")
        self.logger.info(f"  Python:   {platform.python_version()}")
        self.logger.info("########################################################")

        targets = self.config.get("backup_targets", []) or []
        if not targets:
            self.logger.warning("No 'backup_targets' defined in configuration.")

        for target in targets:
            try:
                self.process_target(target)
            except Exception as e:
                self.logger.exception(
                    f"Unhandled error processing target {target.get('name', '?')}: {e}"
                )
                self.stats["errors"] += 1

        gb_freed = self.stats["bytes_freed"] / (1024 ** 3)
        self.logger.info("########## Run Summary ##########")
        self.logger.info(f"  Targets processed : {self.stats['targets']}")
        self.logger.info(f"  Targets skipped   : {self.stats['skipped']}")
        self.logger.info(f"  Backups scanned   : {self.stats['scanned']}")
        self.logger.info(f"  Kept              : {self.stats['kept']}")
        self.logger.info(f"  Deleted           : {self.stats['deleted']}")
        self.logger.info(f"  Errors            : {self.stats['errors']}")
        self.logger.info(f"  Space freed       : {gb_freed:.2f} GB")
        self.logger.info("########## End of Run ##########")

        return 0 if self.stats["errors"] == 0 else 1


# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "VCF Backup Retention Manager - manages retention of "
            "SDDC Manager, NSX, vCenter, and VCF 9 Fleet/Identity/Automation "
            "backups stored on a Linux or Windows server via SCP/SFTP/SMB."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-c", "--config",
                        help="Path to JSON configuration file (required unless --list-presets)")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Show what would be deleted without deleting anything")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable DEBUG-level logging (per-item KEEP decisions)")
    parser.add_argument("--list-presets", action="store_true",
                        help="List available presets and exit")
    args = parser.parse_args()

    if args.list_presets:
        print("Available presets:")
        for name, p in sorted(PRESETS.items()):
            print(f"  - {name:24s}  type={p['type']:9s}  pattern={p['pattern']}")
        sys.exit(0)

    if not args.config:
        parser.error("the following arguments are required: -c/--config")

    manager = BackupRetentionManager(
        config_path=args.config,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    sys.exit(manager.run())


if __name__ == "__main__":
    main()

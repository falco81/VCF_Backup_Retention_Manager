# VCF Backup Retention Manager

Manages retention of VMware Cloud Foundation backups stored on a Linux or
Windows server (received via SCP / SFTP / SMB). Pure Python 3.8+ standard
library; no external dependencies for the retention script itself.

Supports the following VCF backup formats out of the box:

| Component | VCF version | Type | Pattern |
|---|---|---|---|
| SDDC Manager | 5.x and 9.x | file | `vcf-backup-<host>-<domain>-<YYYY-MM-DD-HH-MM-SS>.tar.gz` (and matching `.sha256`) |
| NSX-T / NSX | 5.x and 9.x | directory | `<root>/<node-uuid>/backup-<YYYY-MM-DDTHH_MM_SS>UTC/` |
| NSX inventory summaries | 5.x and 9.x | file | `<root>/<node-uuid>/inventory-<YYYY-MM-DDTHH_MM_SS>UTC.json` |
| vCenter Server (VAMI) | 5.x and 9.x | directory | `<root>/sn_<fqdn>/M_<version>_<YYYYMMDD>-<HHMMSS>_/` |
| Fleet Manager / Identity Broker / Automation | 9.x | directory | `vcf/backups/<cluster>/<version>/<component>/<timestamp>/...tgz` |

---

## Table of contents

1. [Quick start](#quick-start)
2. [Files in this package](#files-in-this-package)
3. [Configuration wizard](#configuration-wizard)
4. [Configuration file (JSON) reference](#configuration-file-json-reference)
5. [Built-in presets](#built-in-presets)
6. [Custom patterns](#custom-patterns)
7. [Worked example: real VCF 5.2.x deployment](#worked-example-real-vcf-52x-deployment)
8. [Installation - Linux](#installation---linux)
9. [Installation - Windows 10](#installation---windows-10)
10. [First run, scheduling, monitoring](#first-run-scheduling-monitoring)
11. [Safety](#safety)

---

## Quick start

The fastest path:

```bash
# Linux (also fine on macOS)
pip install colorama
python3 vcf_retention_wizard.py
# Choose:  3) Autodetect VCF 5.2.x  (or 4) for VCF 9.x)
# Point at your top-level backup folder, e.g. /home/backup
# Confirm what the wizard found

# Test (no deletes!)
python3 vcf_backup_retention.py -c vcf-retention-config.json --dry-run --verbose

# Schedule via cron
sudo crontab -e
# add: 15 3 * * * /usr/bin/python3 /opt/backup_retention_manager/vcf_backup_retention.py -c /opt/backup_retention_manager/vcf-retention-config.json
```

That's it. The rest of this document explains how everything works.

---

## Files in this package

| File | Purpose |
|---|---|
| `vcf_backup_retention.py` | The retention script. No external dependencies. Run it via cron / Task Scheduler. |
| `vcf_retention_wizard.py` | Interactive wizard that builds the JSON config. Requires `colorama`. |
| `config.json` | Example config covering both VCF 5.2.x and VCF 9.x |
| `config-vcf52.json` | Example for VCF 5.2.x only |
| `config-vcf9.json` | Example for VCF 9.x only |
| `README.md` | This file |

---

## Configuration wizard

`vcf_retention_wizard.py` is the recommended way to build a config.

### Requirements

```bash
pip install colorama
```

That's it. Editable default values in prompts use raw terminal mode
(`termios` on Linux/macOS, `msvcrt` on Windows) - no extra packages needed.

### Four setup modes

When the wizard starts:

```
Choose a setup mode:
  1) Simple    - guided setup using built-in VCF presets (recommended)
  2) Advanced  - full control: custom targets, regex patterns, overrides
  3) Autodetect VCF 5.2.x - point at a top folder, wizard finds instances
  4) Autodetect VCF 9.x   - point at a top folder, wizard finds instances
```

**If you already have backups landing on the server, use 3 or 4.** The
wizard scans the folder, finds your instances, identifies their components,
and proposes a complete config. You only confirm or adjust.

If you are setting up before any backup arrives, use `1` (simple). Use `2`
(advanced) only when you need non-VCF backups or custom regex.

### Editable defaults

Where a default value is offered, it is **pre-filled at the cursor** so you
can press Enter to accept, or use Backspace / typing to edit it directly.

```
Where on this server do backups land? (base path): /backup█
                                                          ↑ cursor
```

Keyboard shortcuts inside an editable prompt:

| Key | Action |
|---|---|
| Enter | Accept the current value |
| Backspace | Delete the previous character |
| Ctrl-U | Clear the entire line |
| Ctrl-W | Delete the previous word |
| Ctrl-C | Cancel the wizard |
| Ctrl-D | Cancel (when the line is empty) |

When the wizard runs without a TTY (piped input for automation), it falls
back to a `[default]: ` prompt where empty input means "accept default".

### Mode 3 / 4: Autodetect

You provide one top-level path (e.g. `/home/backup`). The wizard:

1. Scans subdirectories and identifies which ones look like VCF backup
   instances. Anything without a recognised backup is reported as
   "skipped" (typically a freshly set-up environment with no backups
   yet, or unrelated content like logs).
2. For each instance, identifies which components are present (SDDC
   Manager files, NSX backup folders, NSX inventory JSON, vCenter VAMI
   folders, VCF 9 Fleet folders).
3. Guesses VCF version per instance from the components found (Fleet
   present → VCF 9.x, otherwise → VCF 5.2.x).
4. Asks you to confirm each instance and each component, with sensible
   default retention values.

#### Example: real /home/backup with 4 instances

```
Scanning /home/backup ...
  Skipped 2 subdir(s) with no recognised backups:
    - x-vcf  (empty or no recognised content - maybe newly set up?)
    - x-vcf-t  (empty or no recognised content - maybe newly set up?)

  Found 4 candidate instance(s):
    b-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)
    c-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)
    m-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)
    u-vcf   VCF 5.2.x  (sddc_manager, nsx, vcenter)
```

Note that `u-vcf` was correctly detected as having no `inventory-summary`
yet - the wizard skips that component for it.

Resulting target names get an instance prefix so you can tell them apart
in logs:

```
[b-vcf] VCF 5.2.x - SDDC Manager (.tar.gz files)
[b-vcf] VCF 5.2.x - NSX-T (timestamped folders)
[c-vcf] VCF 5.2.x - SDDC Manager (.tar.gz files)
...
```

#### How detection works

- **An instance** is an immediate subdirectory that contains at least
  one item (file or folder, possibly several levels deep) matching one
  of the built-in presets. Empty subdirectories or unrelated content
  are reported but skipped.
- **A component** is detected by checking conventional subfolder names
  first (`sddc-manager-backup`, `cluster-node-backups`, `inventory-summary`,
  `vCenter`, `fleet`); if a hint matches, that subfolder becomes the
  target's `path`. Otherwise, the whole instance directory is used (the
  retention script searches recursively at runtime).
- **VCF version** is guessed: if `vcf9_fleet` is present, the instance
  is VCF 9.x; otherwise VCF 5.2.x. If your autodetect mode does not
  match the detected version, the wizard prints a warning before asking
  you to include or skip the instance.

### Mode 1: Simple

Guided setup with multi-instance support. Steps:

1. (Optionally) load an existing config to add more instances to it.
2. Log file path.
3. **For each instance:**
   - Pick the VCF version (5.2.x or 9.x).
   - Provide an instance label (e.g. `b-vcf`, `prod`).
   - Provide the SFTP root path (default `/home/backup/<label>`).
   - For each component: include? path? keep_days? keep_minimum?
4. "Add another instance?" — repeat as many times as needed.

### Mode 2: Advanced

Full control:

- Optionally load an existing config to add more targets to it.
- Logging settings (level, max file size, rotation count).
- Add targets one at a time:
  - **Preset target**: pick a preset, optionally override `pattern`,
    `min_age_minutes`, mix `keep_days` with `keep_count`.
  - **Custom target**: define everything by hand - `type` (file or
    directory), regex `pattern`, `timestamp_formats`, `recursive`,
    retention. For file mode, the wizard offers a catalog of common
    regex patterns (PostgreSQL dumps, ISO timestamps, date-only
    filenames, mtime fallback, etc.) to pick from or adapt.

### Re-running the wizard with an existing config

All four modes can load an existing config and append to it:

```
Start from an existing config (to add more instances to it)? [y/N]: y
Path to existing config: /opt/backup_retention_manager/vcf-retention-config.json
```

In autodetect modes, components already in the loaded config are
**deduplicated** automatically:

```
Found 4 candidate instance(s):
    b-vcf   VCF 5.2.x  (...)  [4 already in config]
    c-vcf   VCF 5.2.x  (...)  [4 already in config]
    u-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)  [3 already in config]

  --- Instance: u-vcf ---
  Component: NSX inventory summaries (inventory-*.json)
  Detected at: /home/backup/u-vcf/inventory-summary
  Include this component? [Y/n]:
  ...
```

Only new components are added; nothing already present is duplicated.
This makes incremental setup easy - run the wizard once on day 1, run it
again later when more components arrive, and only the diff is asked
about.

### Troubleshooting

- **"Value cannot be empty."** - you pressed Enter on a prompt that
  required input (a name with no default). Type a value.
- **"'foo' is not a valid integer."** - re-enter a number.
- **Cursor stays at start, can't see default** - your terminal does
  not support raw mode (rare). The wizard falls back to `[default]: `
  prompts; press Enter to accept or type a new value.
- **Coloured output looks like garbage** (`\x1b[1;36m...`) - either
  your terminal does not render ANSI (rare on modern Windows 10+ and
  any Linux terminal) or `colorama` is not installed.
- **Wizard exits with `Cancelled by user.`** - you pressed Ctrl-C
  or Ctrl-D. No file is saved.

---

## Configuration file (JSON) reference

The retention script reads a JSON config that looks like this:

```json
{
  "log": {
    "file": "/var/log/vcf-backup-retention.log",
    "level": "INFO",
    "max_size_mb": 10,
    "backup_count": 5
  },
  "backup_targets": [
    {
      "name": "VCF 5.2.x - SDDC Manager",
      "enabled": true,
      "path": "/home/backup/b-vcf/sddc-manager-backup",
      "preset": "sddc_manager",
      "min_age_minutes": 60,
      "retention": {
        "keep_days": 30,
        "keep_minimum": 10
      }
    }
  ]
}
```

### Per-target keys

| Key | Required | Default | Description |
|---|---|---|---|
| `name` | no | path | Display name shown in the log |
| `enabled` | no | `true` | Set to `false` to skip this target without removing it from the config |
| `path` | yes | - | Root directory to scan |
| `preset` | no | - | One of `sddc_manager`, `nsx`, `nsx_inventory`, `vcenter`, `vcf9_fleet`, `generic_timestamp_dir` |
| `type` | no | from preset / `directory` | `file` or `directory` (overrides preset) |
| `pattern` | no | from preset | Custom regex applied to file/dir name (overrides preset) |
| `timestamp_formats` | no | from preset | List of `strptime` formats to parse age from name |
| `recursive` | no | `true` | Walk into subdirectories |
| `min_age_minutes` | no | `60` | Never touch backups younger than this (protects in-flight uploads) |
| `retention.keep_days` | one of two | - | Keep backups newer than X days |
| `retention.keep_count` | one of two | - | Keep the newest N backups (per group) |
| `retention.keep_minimum` | no | `1` | Always keep at least N most recent backups, even if older than `keep_days` |

You must set at least one of `keep_days` or `keep_count`. They can be
combined - both rules are evaluated and a backup is kept if either rule
says so.

### Retention logic

For every group of backups (one group = one parent folder), evaluated
newest first:

1. The newest `keep_minimum` backups are always kept (safety floor).
2. Backups younger than `min_age_minutes` are always kept (protects
   in-flight uploads).
3. If `keep_count` is set, the newest `keep_count` backups are kept.
4. If `keep_days` is set, any backup with age ≤ `keep_days` is kept.
5. Anything else is deleted.

### Per-group retention

The script groups discovered backups by their parent directory and
applies retention to each group separately. For NSX, this means each
manager node UUID keeps its own retention window. For VCF 9 Fleet,
each component (Fleet Manager / Identity Broker / Automation) gets
its own retention. You don't need to configure this per node - it's
automatic.

### `enabled: false`

To temporarily skip a target without removing it from the config:

```json
{
  "name": "VCF 5.2.x - vCenter Server (under maintenance)",
  "enabled": false,
  ...
}
```

The script logs `--- Skipping disabled target: ... ---` and adds
`Targets skipped: N` to the run summary.

---

## Built-in presets

A preset predefines `type`, `pattern`, `timestamp_formats`, and
`recursive` for a known VCF backup format. List them with:

```bash
python3 vcf_backup_retention.py --list-presets
```

### File mode vs directory mode

- **`type: "file"`** - the backup is a single file (`.tar.gz`,
  `.tgz`, `.zip`, `.json`). Pattern matches the **file name**;
  deletion calls `unlink()`.
- **`type: "directory"`** - the backup is a folder containing one or
  more files. Pattern matches the **folder name**; deletion calls
  `rmtree()` on the entire folder.

### How timestamp parsing works

For every preset and custom pattern, age is extracted from the
file/folder name as follows:

1. Apply the regex to the name.
2. If the regex contains capture groups, concatenate them in order
   (no separator) - that's the timestamp string.
3. If the regex has no capture groups, the entire matched name is
   the timestamp string.
4. Try each format in `timestamp_formats` against the string; the
   first that succeeds wins.
5. If none matches, fall back to the file's `mtime`.

This lets one regex pluck the timestamp out of a longer name (like
`vcf-backup-host-2025-04-25-03-00-00.tar.gz` - capture group 1 = the
timestamp), and lets multiple groups represent date and time
separately (like vCenter's `M_..._20260429-104119_` - groups 1+2 =
`20260429104119`).

### Preset: `sddc_manager`

- **Mode:** `file`
- **For:** SDDC Manager file-based backups (VCF 5.x and 9.x).

Files: `vcf-backup-<host>-<domain>-<YYYY-MM-DD-HH-MM-SS>.tar.gz` plus
matching `.sha256` sidecar. Both extensions are managed together, so a
backup is never left as an orphan checksum.

```regex
^vcf-backup-.+-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.(?:tar\.gz|sha256)$
```

```
/home/backup/b-vcf/sddc-manager-backup/
├── vcf-backup-b-w01-mg01-infra-pcr-cz-2026-04-29-10-53-30.tar.gz
├── vcf-backup-b-w01-mg01-infra-pcr-cz-2026-04-29-10-53-30.sha256
├── vcf-backup-b-w01-mg01-infra-pcr-cz-2026-04-29-11-00-45.tar.gz
└── vcf-backup-b-w01-mg01-infra-pcr-cz-2026-04-29-11-00-45.sha256
```

### Preset: `nsx`

- **Mode:** `directory`
- **For:** NSX-T / NSX backups (VCF 5.x and 9.x).

Folders: `backup-<YYYY-MM-DDTHH_MM_SS>UTC/` under each node UUID
directory. The preset also accepts the older `<YYYY-MM-DD-HH-MM-SS>`
format used by VCF 4.x and earlier.

```regex
^(?:
   backup-(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})UTC
 | backup-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})UTC
 | (\d{4}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2})
)$
```

```
/home/backup/b-vcf/cluster-node-backups/
├── 4.2.3.3.0...-78141442-...-10.13.48.22/
│   ├── backup-2026-04-27T10_52_44UTC/
│   ├── backup-2026-04-28T10_52_44UTC/
│   └── backup-2026-04-29T10_52_44UTC/
└── 4.2.3.3.0...-d5bf1442-...-10.13.48.23/
    ├── backup-2026-04-27T10_52_44UTC/
    └── backup-2026-04-28T10_52_44UTC/
```

Retention is applied **per node UUID independently** - 3 NSX managers
in a cluster always have at least `keep_minimum × 3` backups around.
Each `backup-...UTC/` folder is deleted as one unit (with all its
contents).

NSX itself does **not** enforce retention on the SFTP side - this
preset is the most important one to have running.

### Preset: `nsx_inventory`

- **Mode:** `file`
- **For:** NSX inventory JSON summaries emitted alongside backups
  (VCF 5.x and 9.x).

Files: `inventory-<YYYY-MM-DDTHH_MM_SS>UTC.json` under each node UUID.

```regex
^inventory-(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})UTC\.json$
```

```
/home/backup/b-vcf/inventory-summary/
└── 4.2.3.3.0...-78141442-...-10.13.48.22/
    ├── inventory-2026-04-27T10_57_04UTC.json
    ├── inventory-2026-04-28T10_57_04UTC.json
    └── inventory-2026-04-29T10_57_04UTC.json
```

Use the same `keep_days` / `keep_minimum` as your `nsx` target so the
two stay in sync.

### Preset: `vcenter`

- **Mode:** `directory`
- **For:** vCenter Server file-based backups via VAMI (VCF 5.x and 9.x).

VCF 5.2.x layout (two-level): `<root>/sn_<fqdn>/M_<version>_<YYYYMMDD>-<HHMMSS>_/`.
The preset matches the inner timestamp folder. The legacy flat form
(`sn_<ip>M_..._<date>_<time>_<hash>=`) is also accepted.

```regex
^(?:
   M_.+?_(\d{8})-(\d{6})_?
 | sn_.+?_(\d{8})_(\d{6})_.+
)$
```

```
/home/backup/b-vcf/vCenter/
└── sn_b-w01-vc01.infra.pcr.cz/
    ├── M_8.0.3.00800_20260427-104119_/
    ├── M_8.0.3.00800_20260428-104119_/
    └── M_8.0.3.00800_20260429-104119_/
```

vCenter has its own retention setting in VAMI. This preset is mostly a
safety net - keep `keep_minimum` at least as high as the VAMI setting
so the two never disagree.

### Preset: `vcf9_fleet`

- **Mode:** `directory`
- **For:** VCF 9 Fleet Management backups (Fleet Manager,
  VCF Identity Broker, VCF Automation).

Layout: `<root>/<cluster>/<version>/<component>/<timestamp>/<file>.tgz`.
The preset matches the timestamp folder. Retention is per-component
automatically.

### Preset: `generic_timestamp_dir`

- **Mode:** `directory`
- **For:** anything else with timestamp-named folders.

Useful for non-VCF application backups that follow ISO or compact
timestamp conventions, or as a quick test in `--dry-run` mode.

---

## Custom patterns

Drop the `preset` key entirely and define `type`, `pattern`, and
`timestamp_formats` directly. Two important rules for JSON:

1. **Backslashes must be doubled.** `\d` in regex becomes `\\d` in JSON.
2. **The pattern is matched against the file/dir name only**, not the
   full path. Don't use `/` separators in `pattern`.

### File mode examples

#### PostgreSQL pg_dump backups

```
/backup/postgres/
├── prod_db_20260427_030000.sql.gz
├── prod_db_20260428_030000.sql.gz
└── prod_db_20260429_030000.sql.gz
```

```json
{
  "name": "PostgreSQL prod_db dumps",
  "enabled": true,
  "path": "/backup/postgres",
  "type": "file",
  "pattern": "^prod_db_(\\d{8})_(\\d{6})\\.sql\\.gz$",
  "timestamp_formats": ["%Y%m%d%H%M%S"],
  "retention": { "keep_days": 60, "keep_minimum": 10 }
}
```

#### Application zip with ISO timestamp

```json
{
  "name": "MyApp daily zip",
  "enabled": true,
  "path": "/backup/myapp",
  "type": "file",
  "pattern": "^myapp-(\\d{4}-\\d{2}-\\d{2}T\\d{2}-\\d{2}-\\d{2})\\.zip$",
  "timestamp_formats": ["%Y-%m-%dT%H-%M-%S"],
  "retention": { "keep_count": 30, "keep_minimum": 5 }
}
```

#### Date-only filenames

```json
{
  "name": "Log archives",
  "enabled": true,
  "path": "/backup/logs",
  "type": "file",
  "pattern": "^logs-(\\d{4}-\\d{2}-\\d{2})\\.tar\\.bz2$",
  "timestamp_formats": ["%Y-%m-%d"],
  "retention": { "keep_days": 90, "keep_minimum": 30 }
}
```

#### No date in filename - use mtime

```json
{
  "name": "Random-named backups, age by mtime",
  "enabled": true,
  "path": "/backup/scripts",
  "type": "file",
  "pattern": "^backup-.+\\.tar\\.gz$",
  "timestamp_formats": [],
  "retention": { "keep_days": 30, "keep_minimum": 5 }
}
```

Empty `timestamp_formats` ensures parsing fails for every name, and
the script falls back to each file's modification time.

#### Multiple file types in one folder

If the same folder receives several types of backups, define **two
targets pointing at the same path**, each with its own pattern:

```json
[
  {
    "name": "Shared SFTP - SDDC Manager",
    "path": "/backup/sftp-shared",
    "preset": "sddc_manager",
    "retention": { "keep_days": 30, "keep_minimum": 10 }
  },
  {
    "name": "Shared SFTP - NSX exports",
    "path": "/backup/sftp-shared",
    "type": "file",
    "pattern": "^nsx-config-export-(\\d{4}-\\d{2}-\\d{2})\\.zip$",
    "timestamp_formats": ["%Y-%m-%d"],
    "retention": { "keep_days": 60, "keep_minimum": 7 }
  }
]
```

Each target sees only its own files because the patterns are mutually
exclusive.

### Directory mode examples

#### Compact-timestamp folders

```json
{
  "name": "MyService daily folder backups",
  "enabled": true,
  "path": "/backup/myservice",
  "type": "directory",
  "pattern": "^(\\d{8})-(\\d{6})$",
  "timestamp_formats": ["%Y%m%d%H%M%S"],
  "retention": { "keep_days": 14, "keep_minimum": 5 }
}
```

### Combining preset + override

Use a preset and override individual keys. Per-target keys win over
preset values.

```json
{
  "name": "SDDC Manager - sfo-vcf01 only",
  "enabled": true,
  "path": "/backup/vcf52/sddc-manager",
  "preset": "sddc_manager",
  "pattern": "^vcf-backup-sfo-vcf01-sfo-rainpole-io-(\\d{4}-\\d{2}-\\d{2}-\\d{2}-\\d{2}-\\d{2})\\.(?:tar\\.gz|sha256)$",
  "retention": { "keep_days": 30, "keep_minimum": 10 }
}
```

The preset still provides `type`, `timestamp_formats`, and `recursive`;
only `pattern` is overridden.

---

## Worked example: real VCF 5.2.x deployment

A production VCF 5.2.x backup server with 4 active instances:

```
/home/backup/
├── b-vcf/                             # active, all 4 components
│   ├── cluster-node-backups/
│   ├── inventory-summary/
│   ├── sddc-manager-backup/
│   └── vCenter/
├── c-vcf/                             # same
├── m-vcf/                             # same
├── u-vcf/                             # active, no inventory yet
│   ├── cluster-node-backups/
│   ├── sddc-manager-backup/
│   └── vCenter/
├── x-vcf/                             # newly set up, empty
└── x-vcf-t/                           # newly set up, empty
```

Build the config in one shot:

```bash
python3 vcf_retention_wizard.py
# 3) Autodetect VCF 5.2.x
# n (don't load existing)
# /var/log/vcf-backup-retention.log
# /home/backup
```

The wizard reports:

```
Skipped 2 subdir(s) with no recognised backups:
  - x-vcf  (empty or no recognised content - maybe newly set up?)
  - x-vcf-t  (empty or no recognised content - maybe newly set up?)

Found 4 candidate instance(s):
  b-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)
  c-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)
  m-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)
  u-vcf   VCF 5.2.x  (sddc_manager, nsx, vcenter)
```

After confirming everything with default values: **15 targets** in the
config (4+4+4+3). Live `--dry-run` output:

```
[INFO] === Processing target: [b-vcf] VCF 5.2.x - SDDC Manager (.tar.gz files) ===
[INFO]   Found 4 file(s) matching pattern
[DEBUG]   KEEP [1]: vcf-backup-b-w01-mg01-...2026-04-29-11-00-45.tar.gz   (minimum)
[DEBUG]   KEEP [2]: vcf-backup-b-w01-mg01-...2026-04-29-11-00-45.sha256   (minimum)
...
[INFO] === Processing target: [b-vcf] VCF 5.2.x - NSX-T (timestamped folders) ===
[INFO]   Found 1 directory(s) matching pattern
[INFO]   Group '4.2.3.3.0...-78141442-...': 1 backups
[DEBUG]   KEEP [1]: backup-2026-04-29T10_52_44UTC   (minimum)
...
[INFO] ########## Run Summary ##########
[INFO]   Targets processed : 15
[INFO]   Backups scanned   : 22
[INFO]   Kept              : 22
[INFO]   Deleted           : 0
[INFO]   Errors            : 0
```

When more components arrive (e.g. `u-vcf` gets its first
`inventory-summary`, or `x-vcf` starts receiving backups), re-run the
wizard with **load existing** to add only the diff:

```
Found 5 candidate instance(s):
  b-vcf   VCF 5.2.x  (...)  [4 already in config]
  c-vcf   VCF 5.2.x  (...)  [4 already in config]
  m-vcf   VCF 5.2.x  (...)  [4 already in config]
  u-vcf   VCF 5.2.x  (sddc_manager, nsx, nsx_inventory, vcenter)  [3 already in config]
  x-vcf   VCF 5.2.x  (sddc_manager, ...)

  --- Instance: u-vcf ---
  Component: NSX inventory summaries (inventory-*.json)
  ...
```

Only new components get prompts.

---

## Installation - Linux

```bash
sudo mkdir -p /opt/backup_retention_manager
sudo cp vcf_backup_retention.py vcf_retention_wizard.py \
        /opt/backup_retention_manager/
sudo chmod +x /opt/backup_retention_manager/vcf_backup_retention.py

# (optional - the wizard needs colorama, the retention script does not)
sudo pip3 install colorama

# Generate or copy the config
cd /opt/backup_retention_manager
sudo python3 vcf_retention_wizard.py
# … saves vcf-retention-config.json next to the scripts

# Make sure the log directory is writable
sudo touch /var/log/vcf-backup-retention.log
sudo chown root:root /var/log/vcf-backup-retention.log
sudo chmod 640 /var/log/vcf-backup-retention.log
```

---

## Installation - Windows 10

```powershell
# As Administrator
New-Item -ItemType Directory -Force -Path "C:\Tools\vcf-retention"
Copy-Item vcf_backup_retention.py, vcf_retention_wizard.py `
          -Destination "C:\Tools\vcf-retention\"

# colorama is needed only by the wizard
pip install colorama

cd C:\Tools\vcf-retention
python vcf_retention_wizard.py
```

Example Windows config (paths use forward slashes; `pathlib` accepts
both styles):

```json
{
  "log": {
    "file": "C:/ProgramData/vcf-retention/vcf-backup-retention.log",
    "level": "INFO"
  },
  "backup_targets": [
    {
      "name": "VCF 5.2.x - SDDC Manager",
      "enabled": true,
      "path": "D:/backup/b-vcf/sddc-manager-backup",
      "preset": "sddc_manager",
      "retention": { "keep_days": 30, "keep_minimum": 10 }
    }
  ]
}
```

---

## First run, scheduling, monitoring

### Always dry-run first

```bash
# Linux
python3 vcf_backup_retention.py -c vcf-retention-config.json --dry-run --verbose

# Windows
python vcf_backup_retention.py -c vcf-retention-config.json --dry-run --verbose
```

The output shows lines like:

```
[DRY-RUN] Would delete: /home/backup/b-vcf/sddc-manager-backup/vcf-backup-...-2026-04-15-...tar.gz
          (age: 2026-04-15 03:00:00, size: 412.3 MB, reason: older than 30 days ...)
```

Once you are happy, drop `--dry-run`.

### Linux cron

```cron
# /etc/crontab or root's crontab (sudo crontab -e)
MAILTO=admin@example.com
15 3 * * * /usr/bin/python3 /opt/backup_retention_manager/vcf_backup_retention.py -c /opt/backup_retention_manager/vcf-retention-config.json
```

The script exits with code 1 when any error occurs, 0 otherwise.
Combined with `MAILTO`, you only get e-mail when something needs your
attention.

### Windows Task Scheduler (PowerShell)

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python.exe" `
    -Argument '"C:\Tools\vcf-retention\vcf_backup_retention.py" -c "C:\Tools\vcf-retention\vcf-retention-config.json"'
$trigger = New-ScheduledTaskTrigger -Daily -At 3:15am
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "VCF Backup Retention" `
    -Action $action -Trigger $trigger -Principal $principal `
    -Description "Daily retention of VCF backups"
```

### Verifying it works

```bash
tail -f /var/log/vcf-backup-retention.log
ls -l /var/log/vcf-backup-retention.log*
```

Each target's processing ends with a one-line size summary:

```
[INFO] === Processing target: [b-vcf] VCF 5.2.x - SDDC Manager ===
[INFO]   Found 60 file(s) matching pattern
[INFO]   Group '<root>': 60 backups
[INFO]   Target totals: 60 backup(s), size before: 1.46 GB, freed: 800.02 MB, after clean: 700.01 MB
```

In dry-run mode the label is `to free` instead of `freed`.

Each run finishes with an aggregated summary block:

```
########## Run Summary ##########
  Targets processed : 15
  Targets skipped   : 0
  Backups scanned   : 235
  Kept              : 128
  Deleted           : 107
  Errors            : 0
  ----- Capacity -----
  Size before clean : 142.30 GB
  Space freed       : 18.42 GB
  Size after clean  : 123.88 GB
########## End of Run ##########
```

The capacity numbers cover all backups discovered by all targets in this
run, regardless of `enabled` flag (disabled targets are not scanned).

---

## Safety

- **`keep_minimum`** guarantees you never end up with zero backups,
  even if every backup is older than `keep_days`.
- **`min_age_minutes`** (default 60) protects backups currently being
  uploaded.
- **`--dry-run`** shows actions without performing them.
- **Path sanity check** - the script refuses to delete any path that:
  - resolves outside the configured `path`
  - matches a known system root (`/`, `/etc`, `/var`, `C:\`,
    `C:\Windows`, ...)
  - has fewer than 3 path components
- **`enabled: false`** lets you disable a target without removing it,
  useful while diagnosing problems.

If a deletion is refused for safety reasons, the log shows
`SAFETY REFUSED: ...` and `Errors` in the summary increments.

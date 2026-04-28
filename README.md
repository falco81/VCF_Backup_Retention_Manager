# VCF Backup Retention Manager

Cross-platform Python script to manage retention of VMware Cloud Foundation
backups stored on a Linux or Windows backup server (received via SCP / SFTP /
SMB). Runs from `cron` on Linux or Task Scheduler on Windows. No external
dependencies - pure Python 3.8+ standard library.

Supports the following VCF backup formats out of the box:

| Component | VCF version | Type | Filename / Folder pattern |
|---|---|---|---|
| SDDC Manager | 5.x and 9.x | file | `vcf-backup-<host>-<domain>-<YYYY-MM-DD-HH-MM-SS>.tar.gz` |
| NSX-T / NSX | 5.x and 9.x | directory | `<root>/cluster-node-backups/<uuid>/<YYYY-MM-DD-HH-MM-SS>/` (and `node-backups`, `cluster-backups`) |
| vCenter Server (VAMI) | 5.x and 9.x | directory | `sn_<ip>M_<ver>_<YYYYMMDD>_<HHMMSS>_<base64>=` |
| Fleet Manager / VCF Identity Broker / VCF Automation | 9.x | directory | `vcf/backups/<cluster>/<version>/<component>/<timestamp>/...tgz` |

## Quick start

You can either:

1. **Use the configuration wizard** (recommended for most people) - an
   interactive tool that asks questions and writes the JSON for you.
   See the [Configuration wizard](#configuration-wizard) section at the
   end of this document.
2. **Write the JSON config by hand** using the reference below.

The rest of this document describes the JSON config format and how to run
the retention script. The wizard is documented at the very end.

## Files

- `vcf_backup_retention.py` - the retention script (no external dependencies)
- `vcf_retention_wizard.py` - interactive wizard that generates a JSON config
- `config.json` - example configuration covering VCF 5.2.2 + VCF 9
- `config-vcf52.json` - example for VCF 5.2.x only
- `config-vcf9.json` - example for VCF 9.x only
- `README.md` - this file

## Requirements

- Python 3.8 or later
- No third-party libraries (only standard library)

Verify Python is available:

```bash
# Linux
python3 --version

# Windows (PowerShell or cmd)
python --version
```

## Configuration (JSON)

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
      "name": "VCF 5.2.2 - SDDC Manager",
      "path": "/backup/vcf52/sddc-manager",
      "preset": "sddc_manager",
      "min_age_minutes": 60,
      "retention": { "keep_days": 30, "keep_minimum": 7 }
    }
  ]
}
```

### Per-target keys

| Key | Required | Default | Description |
|---|---|---|---|
| `name` | no | path | Display name shown in the log |
| `enabled` | no | `true` | Set to `false` to skip this target without deleting it from the config |
| `path` | yes | - | Root directory to scan |
| `preset` | no | - | One of `nsx`, `sddc_manager`, `vcenter`, `vcf9_fleet`, `generic_timestamp_dir` |
| `type` | no | from preset / `directory` | `file` or `directory` |
| `pattern` | no | from preset | Custom regex applied to file/dir name |
| `timestamp_formats` | no | from preset | List of `strptime` formats to parse age from name |
| `recursive` | no | `true` | Walk into subdirectories |
| `min_age_minutes` | no | `60` | Never touch backups younger than this (protects in-flight uploads) |
| `retention.keep_days` | one of two | - | Keep backups newer than X days |
| `retention.keep_count` | one of two | - | Keep the newest N backups (per group) |
| `retention.keep_minimum` | no | `1` | Always keep at least N most recent backups, even if older than `keep_days` |

You must set at least one of `keep_days` or `keep_count`. They can be combined -
both rules are evaluated and a backup is kept if either rule says so.

### Retention logic

For every group of backups (one group = one parent folder), evaluated newest first:

1. The newest `keep_minimum` backups are always kept (safety floor).
2. Backups younger than `min_age_minutes` are always kept (protects in-flight uploads).
3. If `keep_count` is set, the newest `keep_count` backups are kept.
4. If `keep_days` is set, any backup with age <= `keep_days` is kept.
5. Anything else is deleted.

### Per-group retention (per-node, per-component)

The script groups discovered backups by their parent directory and applies
retention to each group separately. For NSX-T this means each manager node
keeps its own retention window. For VCF 9 Fleet, each component
(Fleet Manager / Identity Broker / Automation) gets its own retention.

## Presets - detailed reference

The script ships with five presets covering the most common VCF backup
formats. A preset is a shortcut: instead of writing the regex, timestamp
parsing rules, and item type yourself, you set `"preset": "..."` and the
script fills in the details. List them at any time:

```bash
python3 vcf_backup_retention.py --list-presets
```

### File mode vs directory mode

Each target operates in one of two modes (decided by the preset, or by an
explicit `type` key):

- **`type: "file"`** - the backup is a single file (typically `.tar.gz`,
  `.tgz`, `.zip`). The pattern matches the **file name**; deletion calls
  `unlink()` on that file. Size is the file size from `stat()`.
- **`type: "directory"`** - the backup is a folder containing one or more
  files. The pattern matches the **folder name**; deletion calls
  `rmtree()` on the entire folder. Size is the recursive total of all
  files inside.

Some VCF components write a single archive per backup (file mode); others
create a folder per backup (directory mode). Custom targets can use either.

### How timestamp parsing works

For every preset and custom pattern, the script extracts the backup's age
from the file/folder name as follows:

1. Apply the regex to the name.
2. If the regex contains **capture groups**, concatenate them in order
   (no separator) - that's the timestamp string.
3. If the regex has **no capture groups**, the entire matched name is the
   timestamp string.
4. Try each format in `timestamp_formats` against the string with
   `datetime.strptime()`; the first one that succeeds wins.
5. If none matches, fall back to the file's `mtime`.

This lets one regex pluck the timestamp out of a longer name (like
`vcf-backup-host-2025-04-25-03-00-00.tar.gz` -> capture group 1 = the
timestamp), and lets multiple capture groups represent date and time
separately (like vCenter's `sn_..._20250425_030015_...` -> groups 1+2 =
`20250425030015`).

---

### Preset: `sddc_manager`

- **Mode:** `file`
- **Used for:** SDDC Manager file-based backups (VCF 5.x and 9.x).

SDDC Manager backs itself up to SFTP as a single encrypted tarball per
backup, named according to a fixed convention:
`vcf-backup-<hostname-with-dashes>-<domain-with-dashes>-<YYYY-MM-DD-HH-MM-SS>.tar.gz`.

#### Pattern

```regex
^vcf-backup-.+-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.tar\.gz$
```

The capture group extracts the timestamp portion, parsed with
`%Y-%m-%d-%H-%M-%S`.

#### Layout on disk

VCF triggers backups not only on schedule (default daily at 04:02) but
also after every state change, so multiple files per day are normal:

```
/backup/vcf52/sddc-manager/
├── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-23-04-02-00.tar.gz
├── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-24-04-02-00.tar.gz
├── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-25-04-02-00.tar.gz
├── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-25-14-30-22.tar.gz   <- state change
└── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-26-04-02-00.tar.gz
```

#### Config

```json
{
  "name": "VCF 5.2.2 - SDDC Manager",
  "enabled": true,
  "path": "/backup/vcf52/sddc-manager",
  "preset": "sddc_manager",
  "retention": {
    "keep_days": 30,
    "keep_minimum": 10
  }
}
```

#### What happens

All `.tar.gz` files in the path (recursive) form a single group `<root>`.
With the config above, files older than 30 days are deleted, but the 10
newest files are always kept regardless of age.

---

### Preset: `nsx`

- **Mode:** `directory`
- **Used for:** NSX-T / NSX file-based backups (VCF 5.x and 9.x).

NSX runs three kinds of backups (cluster / cluster-node / node) and
creates a timestamped folder for each. A single SFTP target therefore
contains three top-level folders, each holding subfolders per node UUID,
each containing timestamp folders.

#### Pattern

```regex
^\d{4}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}[-_]\d{2}$
```

No capture groups - the whole folder name is the timestamp, parsed as
`%Y-%m-%d-%H-%M-%S` (with `_` separator as fallback).

#### Layout on disk

```
/backup/vcf52/nsx/
├── cluster-backups/
│   ├── 2025-04-23-03-00-00/
│   │   └── cluster.tar.gz
│   ├── 2025-04-24-03-00-00/
│   └── 2025-04-25-03-00-00/
├── cluster-node-backups/
│   ├── abcd1234-...-1234-10.0.0.11/
│   │   ├── 2025-04-23-03-00-00/
│   │   ├── 2025-04-24-03-00-00/
│   │   └── 2025-04-25-03-00-00/
│   ├── abcd1234-...-5678-10.0.0.12/
│   │   └── ...
│   └── abcd1234-...-9abc-10.0.0.13/
│       └── ...
└── node-backups/
    ├── abcd1234-...-1234-10.0.0.11/
    │   └── ...
    └── ...
```

#### Config

```json
{
  "name": "VCF 5.2.2 - NSX-T",
  "enabled": true,
  "path": "/backup/vcf52/nsx",
  "preset": "nsx",
  "retention": {
    "keep_days": 14,
    "keep_minimum": 7
  }
}
```

#### What happens

Retention is applied **per parent folder independently** - each NSX node
and each backup type gets its own pool of N most recent backups. With
3 NSX managers in a cluster you'll always have at least
`keep_minimum * 3` (cluster-node) + `keep_minimum * 3` (node) +
`keep_minimum * 1` (cluster) backups around.

NSX itself does **not** enforce retention on the SFTP side - that's
exactly why this preset is the most important one to have running.

---

### Preset: `vcenter`

- **Mode:** `directory`
- **Used for:** vCenter Server file-based backups via VAMI (VCF 5.x and 9.x).

vCenter writes each backup as a folder with a long structured name:
`sn_<ip>M_<version>_<YYYYMMDD>_<HHMMSS>_<base64-ish>=`. Inside the
folder are the actual backup files.

#### Pattern

```regex
^sn_.+_(\d{8})_(\d{6})_.+$
```

Two capture groups: date (`\d{8}`) and time (`\d{6}`). The script
concatenates them and parses with `%Y%m%d%H%M%S`.

#### Layout on disk

```
/backup/vcf52/vcenter/
├── sn_192.168.1.10M_8.0.300000_20250423_030015_KWER6DG...UEf=/
│   ├── backup-metadata.json
│   ├── full_backup/
│   └── ...
├── sn_192.168.1.10M_8.0.300000_20250424_030022_X7DKL2N...A1S=/
└── sn_192.168.1.10M_8.0.300000_20250425_030008_R3QWMK7...E4T=/
```

#### Config

```json
{
  "name": "VCF 5.2.2 - vCenter Server",
  "enabled": true,
  "path": "/backup/vcf52/vcenter",
  "preset": "vcenter",
  "retention": {
    "keep_days": 30,
    "keep_minimum": 7
  }
}
```

#### What happens

vCenter itself enforces retention via the VAMI setting "Number of
backups to retain". This preset is mostly a safety net - keep the
script's `keep_minimum` at least as high as the VAMI setting so the
two never disagree.

---

### Preset: `vcf9_fleet`

- **Mode:** `directory`
- **Used for:** VCF 9 Fleet Management backups - Fleet Manager,
  VCF Identity Broker (VIDB), and VCF Automation (VCFA).

In VCF 9 these three components share a single SFTP root and a structured
path: `<root>/<cluster-name>/<version>/<component-name>/<timestamp>/<file>.tgz`.

#### Pattern

In the actual JSON the regex is one line; expanded for readability:

```regex
^(
   \d{4}-\d{2}-\d{2}[-T_]\d{2}[-:]\d{2}[-:]\d{2}Z?    # ISO-style: 2025-04-25T03-00-00
 | \d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}                # NSX-style: 2025-04-25-03-00-00
 | \d{8}[-T_]\d{6}                                    # compact:   20250425-030015
)$
```

The whole timestamp is captured and tried against several `strptime`
formats in turn (`%Y-%m-%dT%H-%M-%S`, `%Y-%m-%d-%H-%M-%S`,
`%Y%m%d-%H%M%S`, etc.).

#### Layout on disk

```
/backup/vcf9/fleet/
└── cluster-1/
    └── 9.0.0/
        ├── fleet-manager/
        │   ├── 2025-04-23T03-00-00/
        │   │   └── backup.tgz
        │   └── 2025-04-25T03-00-00/
        ├── identity-broker/
        │   ├── 2025-04-23T03-00-00/
        │   └── 2025-04-25T03-00-00/
        └── vcf-automation/
            ├── 2025-04-23T03-00-00/
            └── 2025-04-25T03-00-00/
```

#### Config

```json
{
  "name": "VCF 9 - Fleet Management",
  "enabled": true,
  "path": "/backup/vcf9/fleet",
  "preset": "vcf9_fleet",
  "retention": {
    "keep_days": 14,
    "keep_minimum": 5
  }
}
```

#### What happens

Retention is applied **per component independently** (one group per
component folder). Each timestamp folder is deleted as one unit -
including the `.tgz` inside it.

VCF 9 has its own retention setting in Fleet Management UI ("Enable
retention policy"). If that's enabled, this preset is a safety net;
if not, this preset is your only retention.

---

### Preset: `generic_timestamp_dir`

- **Mode:** `directory`
- **Used for:** anything else with timestamp-named folders.

Same pattern as `vcf9_fleet`, just neutrally named. Useful when:

- You have application backups (non-VCF) using ISO or compact timestamps.
- You're not sure which preset fits and want to try the most permissive one.
- You're testing in `--dry-run` mode and want to see what would match.

The same caveats apply: timestamp must be in the folder name; the script
falls back to `mtime` if it can't parse the name.

---

## Installation - Linux

```bash
sudo mkdir -p /opt/vcf-retention
sudo cp vcf_backup_retention.py config.json /opt/vcf-retention/
sudo chmod +x /opt/vcf-retention/vcf_backup_retention.py

# Edit paths and retention values for your environment
sudo nano /opt/vcf-retention/config.json

# Make sure log directory is writable
sudo touch /var/log/vcf-backup-retention.log
sudo chown root:root /var/log/vcf-backup-retention.log
sudo chmod 640 /var/log/vcf-backup-retention.log
```

### Linux cron

Add to root's crontab (`sudo crontab -e`):

```cron
# VCF backup retention - daily at 03:15
15 3 * * * /usr/bin/python3 /opt/vcf-retention/vcf_backup_retention.py -c /opt/vcf-retention/config.json >/dev/null 2>&1
```

To get an email only on errors, set `MAILTO=...` at the top of the crontab.
The script exits with code 1 if any error occurred, 0 otherwise. Pair this
with `MAILTO` and a non-zero exit will produce mail.

## Installation - Windows 10

```powershell
# Create installation folder (PowerShell as Administrator)
New-Item -ItemType Directory -Force -Path "C:\Tools\vcf-retention"
Copy-Item vcf_backup_retention.py, config.json -Destination "C:\Tools\vcf-retention\"

# Adjust config to use Windows paths
notepad C:\Tools\vcf-retention\config.json
```

Example Windows config (paths use forward slashes or escaped backslashes -
both work with Python's `pathlib`):

```json
{
  "log": {
    "file": "C:/ProgramData/vcf-retention/vcf-backup-retention.log",
    "level": "INFO",
    "max_size_mb": 10,
    "backup_count": 5
  },
  "backup_targets": [
    {
      "name": "VCF 5.2.2 - SDDC Manager",
      "path": "D:/backup/vcf52/sddc-manager",
      "preset": "sddc_manager",
      "retention": { "keep_days": 30, "keep_minimum": 7 }
    },
    {
      "name": "VCF 5.2.2 - NSX-T",
      "path": "D:/backup/vcf52/nsx",
      "preset": "nsx",
      "retention": { "keep_days": 30, "keep_minimum": 7 }
    }
  ]
}
```

### Windows Task Scheduler (GUI)

1. Open Task Scheduler.
2. Action -> Create Task...
3. **General** tab:
   - Name: `VCF Backup Retention`
   - "Run whether user is logged on or not"
   - "Run with highest privileges"
4. **Triggers** tab -> New: Daily at 03:15.
5. **Actions** tab -> New:
   - Program: `C:\Windows\System32\cmd.exe` (or full path to `python.exe`)
   - If using cmd: arguments
     `/c python "C:\Tools\vcf-retention\vcf_backup_retention.py" -c "C:\Tools\vcf-retention\config.json"`
   - Or call Python directly:
     - Program: `C:\Python312\python.exe` (or wherever Python is installed)
     - Arguments: `"C:\Tools\vcf-retention\vcf_backup_retention.py" -c "C:\Tools\vcf-retention\config.json"`
6. **Conditions / Settings**: leave default or tighten as you wish.

### Windows Task Scheduler (PowerShell, scripted)

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python.exe" `
    -Argument '"C:\Tools\vcf-retention\vcf_backup_retention.py" -c "C:\Tools\vcf-retention\config.json"'

$trigger = New-ScheduledTaskTrigger -Daily -At 3:15am

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "VCF Backup Retention" `
    -Action $action -Trigger $trigger -Principal $principal `
    -Description "Daily retention of VCF backups"
```

## First run - always dry-run!

The script never deletes anything in dry-run mode. Always run this first
to see what would happen:

**Linux**

```bash
sudo python3 /opt/vcf-retention/vcf_backup_retention.py \
     -c /opt/vcf-retention/config.json --dry-run --verbose
```

**Windows**

```powershell
python C:\Tools\vcf-retention\vcf_backup_retention.py `
     -c C:\Tools\vcf-retention\config.json --dry-run --verbose
```

The output shows lines like:

```
[DRY-RUN] Would delete: /backup/vcf52/sddc-manager/vcf-backup-...-2025-01-15-03-00-00.tar.gz
          (age: 2025-01-15 03:00:00, size: 412.3 MB, reason: older than 30 days ...)
```

Once you are happy, drop `--dry-run` and let the cron / scheduled task run it.

## Custom patterns (when no preset fits)

You can drop the `preset` key entirely and define `type`, `pattern`, and
`timestamp_formats` directly on the target. Two important rules for JSON:

1. **Backslashes must be doubled.** `\d` in regex becomes `\\d` in JSON.
2. **The pattern is matched against the file/dir name only**, not the
   full path. Don't use `/` separators in `pattern`.

### Custom file mode - examples

#### PostgreSQL pg_dump backups

Two separate number groups in the filename.

```
/backup/postgres/
├── prod_db_20250423_030000.sql.gz
├── prod_db_20250424_030000.sql.gz
├── prod_db_20250425_030000.sql.gz
└── prod_db_20250426_030000.sql.gz
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

The two groups `(\d{8})` and `(\d{6})` are concatenated to
`20250425030000`, parsed as `%Y%m%d%H%M%S`.

#### Application zip backups with ISO date

Single capture group for the entire timestamp.

```
/backup/myapp/
├── myapp-2025-04-23T03-00-00.zip
├── myapp-2025-04-24T03-00-00.zip
└── myapp-2025-04-25T03-00-00.zip
```

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

Here `keep_count: 30` keeps the 30 newest, `keep_minimum: 5` is a safety
floor (in this scenario `keep_minimum` is redundant because `keep_count`
already keeps more, but it's good practice to set both).

#### Log archives with date-only timestamp

When the filename contains only a date (no time).

```
/backup/logs/
├── logs-2025-04-23.tar.bz2
├── logs-2025-04-24.tar.bz2
└── logs-2025-04-25.tar.bz2
```

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

A date-only timestamp is treated as midnight, which works correctly for
daily comparisons.

#### Custom prefix + suffix, no preset

Plain `.tgz` archives with a prefix you choose.

```
/backup/vmware-misc/
├── nsx-edge-config-2025-04-23-03-00-00.tgz
├── nsx-edge-config-2025-04-24-03-00-00.tgz
└── nsx-edge-config-2025-04-25-03-00-00.tgz
```

```json
{
  "name": "NSX edge config exports",
  "enabled": true,
  "path": "/backup/vmware-misc",
  "type": "file",
  "pattern": "^nsx-edge-config-(\\d{4}-\\d{2}-\\d{2}-\\d{2}-\\d{2}-\\d{2})\\.tgz$",
  "timestamp_formats": ["%Y-%m-%d-%H-%M-%S"],
  "retention": { "keep_days": 30, "keep_minimum": 5 }
}
```

#### Files where the date is not in the name

If your filename has no parseable date (e.g. `backup-final.tar.gz` or
`dump_v3.zip`), force the script to use the file's `mtime` as the age
by giving an empty `timestamp_formats` list:

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

Empty `timestamp_formats` ensures parsing fails for every name, and the
script falls back to each file's modification time. Less reliable than
parsing from the name, but workable.

#### Multiple file types in the same directory

If the same folder gets multiple types of backups dumped into it, define
**two targets pointing at the same path**, each matching its own files:

```
/backup/sftp-shared/
├── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-25-04-02-00.tar.gz
├── vcf-backup-sfo-vcf01-sfo-rainpole-io-2025-04-26-04-02-00.tar.gz
├── nsx-config-export-2025-04-25.zip
└── nsx-config-export-2025-04-26.zip
```

```json
[
  {
    "name": "Shared SFTP - SDDC Manager",
    "enabled": true,
    "path": "/backup/sftp-shared",
    "preset": "sddc_manager",
    "retention": { "keep_days": 30, "keep_minimum": 10 }
  },
  {
    "name": "Shared SFTP - NSX exports",
    "enabled": true,
    "path": "/backup/sftp-shared",
    "type": "file",
    "pattern": "^nsx-config-export-(\\d{4}-\\d{2}-\\d{2})\\.zip$",
    "timestamp_formats": ["%Y-%m-%d"],
    "retention": { "keep_days": 60, "keep_minimum": 7 }
  }
]
```

The two targets see only their own files because the patterns are
mutually exclusive.

### Custom directory mode - examples

#### Compact-timestamp folders

```
/backup/myservice/
├── 20250423-030000/
│   └── data/
├── 20250424-030000/
└── 20250425-030000/
```

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

If a preset is almost right but you want to tighten one rule, set the
preset and override individual keys. Per-target keys win over preset values.

For instance, if you only want to manage SDDC Manager backups from one
specific host (and ignore others in the same folder):

```json
{
  "name": "SDDC Manager - sfo-vcf01 only",
  "enabled": true,
  "path": "/backup/vcf52/sddc-manager",
  "preset": "sddc_manager",
  "pattern": "^vcf-backup-sfo-vcf01-sfo-rainpole-io-(\\d{4}-\\d{2}-\\d{2}-\\d{2}-\\d{2}-\\d{2})\\.tar\\.gz$",
  "retention": { "keep_days": 30, "keep_minimum": 10 }
}
```

The preset still provides `type`, `timestamp_formats`, and `recursive`;
only `pattern` is overridden.

## Safety

- `keep_minimum` guarantees you never end up with zero backups, even if
  every backup is older than `keep_days`.
- `min_age_minutes` protects backups currently being uploaded.
- `--dry-run` shows actions without performing them.
- The script refuses to delete any path that:
  - resolves outside the configured `path`;
  - matches a known system root (`/`, `/etc`, `/var`, `C:\`, `C:\Windows`, ...);
  - has fewer than 3 path components.

## Verifying it works

**Linux**

```bash
tail -f /var/log/vcf-backup-retention.log
ls -l /var/log/vcf-backup-retention.log*
```

**Windows**

```powershell
Get-Content C:\ProgramData\vcf-retention\vcf-backup-retention.log -Wait -Tail 50
```

Each run ends with a summary block:

```
########## Run Summary ##########
  Targets processed : 4
  Backups scanned   : 235
  Kept              : 128
  Deleted           : 107
  Errors            : 0
  Space freed       : 12.34 GB
########## End of Run ##########
```

---

## Configuration wizard

If you don't want to write JSON by hand, the package includes an interactive
wizard (`vcf_retention_wizard.py`) that asks questions and produces a valid
config file ready for use with `vcf_backup_retention.py`.

### Requirements

The wizard requires only **`colorama`** (for colored prompts). The retention
script itself has zero external dependencies; the wizard is the only place
that needs `colorama`.

```bash
pip install colorama
```

That's all. No `pyreadline3`, no `prompt_toolkit`, nothing else - editable
default values are implemented via raw terminal mode (Linux: `termios`;
Windows: `msvcrt`), both available in the Python standard library.

### Running the wizard

```bash
# Linux
python3 vcf_retention_wizard.py

# Windows
python vcf_retention_wizard.py
```

Optional: pass `-o` to choose the default output filename:

```bash
python3 vcf_retention_wizard.py -o config-vcf52.json
```

### Two setup paths

When the wizard starts it asks which mode you want:

```
Choose a setup mode:
  1) Simple   - guided setup using built-in VCF presets (recommended)
  2) Advanced - full control: custom targets, regex patterns, overrides
```

#### Simple mode

Guided setup for the standard VCF 5.2.x or VCF 9.x components. Steps:

1. **Pick the VCF version** - either `1) VCF 5.2.x only` or
   `2) VCF 9.x only`. If you have both, run the wizard twice and either
   merge the resulting files or keep them as two cron jobs.
2. **Base backup path** - where backups land on your server. Default
   `/backup`, editable.
3. **Log file path** - default `/var/log/vcf-backup-retention.log`,
   editable.
4. **Per-component questions** - for each VCF component, the wizard asks:
   - Manage this component? (Y/n)
   - Backup path (with a sensible auto-generated default)
   - Keep backups for how many days
   - Always keep at least how many newest backups (safety floor)

Components offered:

- VCF 5.2.x: SDDC Manager, NSX-T, vCenter Server
- VCF 9.x: SDDC Manager, NSX, vCenter Server, Fleet Management
  (Fleet Manager + Identity Broker + Automation)

The wizard fills in the rest (regex pattern, timestamp parsing, item type)
from the built-in presets.

#### Advanced mode

Full control. Adds these capabilities on top of simple mode:

- **Logging settings** - log level, max file size, rotation count
- **Multiple targets** - add one target at a time, then either add another
  or finish
- **Preset target with overrides** - pick a preset, then optionally set
  per-target `min_age_minutes`, mix `keep_days` with `keep_count`,
  override the regex pattern of the preset
- **Custom targets** - skip the presets entirely and define everything by
  hand: type (file or directory), regex pattern, timestamp formats,
  recursion, retention

When you create a **custom file-mode target**, the wizard shows a catalog
of common regex patterns to pick from:

```
Pick a pattern for file names, or write your own:
  1) Single timestamp like '...-2025-04-25-03-00-00.tar.gz'
     regex:    ^.+-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\..+$
     formats:  %Y-%m-%d-%H-%M-%S
     matches:  myapp-2025-04-25-03-00-00.tar.gz

  2) Date+time as separate groups like '..._20250425_030000.sql.gz'
     regex:    ^.+_(\d{8})_(\d{6})\..+$
     formats:  %Y%m%d%H%M%S
     matches:  prod_db_20250425_030000.sql.gz

  3) ISO timestamp like '...-2025-04-25T03-00-00.zip'
  4) Date only like '...-2025-04-25.tar.bz2'
  5) Compact timestamp like '...20250425-030000.tgz'
  6) No date in name - use file mtime
  7) Custom - write your own pattern
```

After picking, you can either use the pattern as-is or tweak it. Picking
"Custom" lets you write any regex; the wizard validates that it compiles
before saving the config.

For **custom directory-mode targets** the wizard offers a smaller catalog
of timestamp folder patterns (NSX-style, ISO, compact) plus a
"write your own" option.

### Editable defaults

Everywhere a default value is offered, it is **pre-filled at the cursor**
so you can press Enter to accept, or use Backspace / typing to edit it
directly. No need to retype the whole default just to change one character.

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

Editable defaults work on Linux, macOS, and Windows 10+ without any extra
packages. When the wizard runs without a TTY (e.g. piped input for
automation), it automatically falls back to a `[default]: ` style prompt
where empty input is treated as "accept default".

### After saving

The wizard validates the config and saves it to the path you specify
(default `vcf-retention-config.json`). It then prints the exact commands
to run:

```
Next steps:
  1. Test (no deletes):  python3 vcf_backup_retention.py -c config.json --dry-run --verbose
  2. Run live:           python3 vcf_backup_retention.py -c config.json
  3. Schedule via cron (Linux) or Task Scheduler (Windows).
     See README.md for details.
```

Always run with `--dry-run --verbose` first to confirm the wizard's output
matches what you actually want before scheduling it.

### Editing a wizard-generated config later

The output is plain JSON; you can open it in any editor and tweak any value
by hand later. To adjust retention numbers, re-add a target you previously
declined, or temporarily disable a target, just edit the file - the wizard
isn't required for changes.

To keep a target permanently in the file but skip it during runs, set
`"enabled": false`:

```json
{
  "name": "VCF 5.2.x - vCenter Server",
  "enabled": false,
  ...
}
```

This is documented under [Per-target keys](#per-target-keys) above.

### Troubleshooting the wizard

- **"Value cannot be empty."** - you pressed Enter on a prompt that
  required input (a name or path with no default). Type a value.
- **"'foo' is not a valid integer."** - re-enter a number.
- **Cursor stays at start, can't see default** - your terminal doesn't
  support raw mode, or stdin/stdout aren't a TTY (running under some IDEs
  or remote shells). The wizard falls back to `[default]: ` mode in that
  case; just press Enter to accept or type a new value.
- **Colored output looks like garbage** (`\x1b[1;36m...`) - either your
  terminal doesn't render ANSI (rare on modern Windows 10+ and any Linux
  terminal) or `colorama` isn't installed. Install with
  `pip install colorama`.
- **Wizard exits with `Cancelled by user.`** - you pressed Ctrl-C or
  Ctrl-D, or hit EOF on piped input. No file is saved.

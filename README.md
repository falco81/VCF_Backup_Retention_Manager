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

## Files

- `vcf_backup_retention.py` - the script
- `config.json` - example configuration covering VCF 5.2.2 + VCF 9
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

### Available presets

```bash
python3 vcf_backup_retention.py --list-presets
```

Output:

```
- generic_timestamp_dir     type=directory  pattern=...
- nsx                       type=directory  pattern=^\d{4}[-_]\d{2}[-_]\d{2}...
- sddc_manager              type=file       pattern=^vcf-backup-.+-(\d{4}-...
- vcenter                   type=directory  pattern=^sn_.+_(\d{8})_(\d{6})_.+$
- vcf9_fleet                type=directory  pattern=...
```

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

## Custom backup formats

If you have backups with a different naming scheme, define a custom target
without `preset`:

```json
{
  "name": "Custom backup",
  "path": "/backup/custom",
  "type": "file",
  "pattern": "^backup_(\\d{8})_(\\d{6})\\.zip$",
  "timestamp_formats": ["%Y%m%d%H%M%S"],
  "recursive": true,
  "retention": { "keep_days": 60, "keep_minimum": 3 }
}
```

Notes on patterns:

- Patterns are matched against the **name** of the file or directory only,
  not its full path.
- If the regex contains capture groups, their concatenation (in order) is
  parsed as the timestamp using the formats in `timestamp_formats`. If
  there are no groups, the entire name is parsed.
- If parsing fails for any reason, the script falls back to the file's
  modification time (`mtime`).
- In JSON, every backslash in a regex must be doubled: `\d` becomes `\\d`.

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

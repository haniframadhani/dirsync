# dirsync

Directory copy with resume, integrity verification, and progress tracking.

## Usage

```
python3 dirsync.py <source> <destination> [options]
```

## Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview what would be copied without copying |
| `--verify-only` | Re-verify destination files without copying |
| `--retries N` | Retry attempts per file (default: 3) |
| `--jobs N` | Parallel workers for small files (default: 4) |
| `--exclude PATTERN` | Exclude files matching pattern (repeatable) |
| `--include PATTERN` | Only copy files matching pattern (repeatable) |
| `--preserve-links` | Copy symlinks instead of their targets |
| `--preserve-special` | Copy special files (FIFOs, sockets, devices) |
| `--force` | Force copy even if source was modified during copy |
| `--verbose, -v` | Verbose logging |
| `--quiet, -q` | Suppress output |

## Examples

```
python3 dirsync.py /mnt/media /mnt/backup
python3 dirsync.py /mnt/media /mnt/backup --dry-run
python3 dirsync.py /mnt/media /mnt/backup --retries 5 --jobs 8
python3 dirsync.py /mnt/media /mnt/backup --exclude "*.tmp" --exclude "*.log"
python3 dirsync.py /mnt/media /mnt/backup --verify-only
```

## Features

- Resumable copy (Ctrl+C / crash / power loss safe)
- SHA-256 integrity verification
- SQLite-based state tracking
- Parallel small-file copying
- Source mutation detection

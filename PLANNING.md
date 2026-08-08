# Directory Copy Script - Project Plan (Revised)

## Summary

Develop a Python 3 script (stdlib only, no third-party dependencies) that reliably copies a large directory (100 GB or more) between physical storage devices. The script must support resuming interrupted copy operations and verify file integrity to prevent corruption.

The solution should be suitable for copying a large number of files where manual verification is impractical.

---

# Objectives

* Copy an entire directory while preserving its structure.
* Resume interrupted copy operations without restarting from the beginning.
* Verify every copied file using cryptographic hashes to detect corruption.
* Preserve all original files without modification.
* Support copying between different physical drives.

---

# Language Choice

**Python 3** is selected over Bash because:

* State management (SQLite) and atomic writes are straightforward.
* Offset-based file resume is practical with file seeks.
* Graceful signal handling (SIGINT/SIGTERM) is cleanly supported.
* Complex logic (source-mutation checks, parallel small-file copying) is maintainable.

Use the standard library only (`os`, `shutil`, `hashlib`, `sqlite3`, `argparse`, `signal`, `threading`, `concurrent.futures`, `time`, `logging`). Zero pip dependencies keeps the tool portable.

---

# Features

## 1. Resumable Copy

The script must continue an interrupted copy operation caused by:

* User interruption (Ctrl+C / SIGINT)
* SIGTERM
* System shutdown or restart
* Power outage
* Script crash
* Drive disconnection

When restarted, the script must:

* Skip files already successfully copied and verified.
* Resume partially copied files from the last known byte offset.
* Continue from the remaining files.
* Avoid recopying completed files.

### Resumption semantics

* A file whose state is `verified` is skipped (after an existence check on the destination file — a cheap `os.stat`).
* A file whose state is `in-progress` (with a recorded offset) is resumed from that offset on the destination, while the source hash is computed over the *entire* source file (the hash accumulator is read from the start so the final hash covers all bytes).
* A file with no state is copied from scratch.
* On resumption, if the destination file is missing (e.g., externally deleted), the file is recopied from scratch.

### Signal handling

* On SIGINT/SIGTERM, the current file's byte offset is written to state as `in-progress`, then the script exits cleanly. This guarantees a known resume point.
* A second Ctrl+C within a short window forces immediate exit (with state saved to the last completed file).

---

## 2. File Integrity Verification

To prevent silent corruption during transfer:

* Compute the SHA-256 hash of the source file **in the same streaming pass that copies it** (single source read — hash accumulator updated as bytes are read for writing). This halves source disk I/O compared to copying first and hashing second.
* After the copy completes, read the destination file and compute its SHA-256 in chunks.
* Compare the hashes.
* Mark the file as `verified` only if the hashes match.

If verification fails:

* Delete the corrupted destination file.
* Reset the file's state to `pending`.
* Retry the copy (configurable number of attempts, default 3).
* Report persistent failures in the log and summary.

### Resume-aware hashing

* For an `in-progress` file being resumed, the source hash must be computed over the full source (read from byte 0), not from the resume offset, so the final hash always covers the entire file.
* The destination bytes already copied are re-read as part of verification only after the copy is fully complete. Verification is always a full read of the destination.

---

## 3. Progress Tracking

Use **SQLite** as the persistent state database. Rationale:

* With tens of thousands of files, rewriting a JSON file after every file is O(n) per write → O(n²) total. SQLite inserts are cheap and transactional.
* SQLite survives power loss via WAL journaling.
* Atomicity is inherent: each `COMMIT` is a durable state update.

Schema:

```
files (
    rel_path     TEXT PRIMARY KEY,   -- path relative to source root
    size         INTEGER,            -- source size in bytes
    mtime_ns     INTEGER,            -- source mtime (epoch ns) for mutation detection
    status       TEXT,               -- pending | in-progress | verified | failed
    offset       INTEGER,            -- bytes already on destination (for in-progress)
    attempts     INTEGER,            -- retry count
    dest_missing INTEGER            -- flag set if dest was found missing on resume
)

meta (
    key TEXT PRIMARY KEY,
    value TEXT
)
```

`meta` stores `source_root`, `destination_root`, `started_at`, `version`. The script refuses to run if `source_root`/`destination_root` in state do not match the CLI arguments (prevents stale-state skips against a different destination).

The state DB lives on the **destination device** next to the copy root (e.g., `dest/.copy-state.db`), so it is not lost if the source drive is disconnected. It is excluded from the copy.

---

## 4. Progress Reporting

Display useful progress information, including:

* Current file being copied
* Total files copied
* Total data copied
* Remaining files
* Transfer speed (bytes/sec over a rolling window)
* Estimated remaining time (ETA) based on bytes remaining / rolling speed
* Percentage complete by bytes

Use a single-line, self-updating terminal output (carriage-return refresh) with a fallback to periodic log lines when not a TTY.

---

## 5. Error Handling

Handle common failures gracefully, including:

* Read errors
* Write errors
* Permission errors
* Insufficient disk space
* Unexpected interruption
* Source file modified during copy

Generate a log file (`dest/.copy.log`) for troubleshooting, in addition to console output.

### Source mutation detection

* Record `size` and `mtime_ns` of each source file at copy time.
* If a file's size or mtime changes between the pre-copy snapshot and the post-copy verification, the copy is considered stale: report it, mark `failed`, and do not loop retrying forever. Add `--force` to override and copy the new version.
* On resume, if the source file's size/mtime differ from what state recorded, treat the file as `pending` again (re-snapshot) rather than trusting the old record.

### Retry policy

* On verification failure, transient errors, or write errors: delete the destination file, reset to `pending`, and retry up to `--retries` (default 3) with backoff.
* Non-transient errors (permission denied, no space left) are reported and the run continues with remaining files; the summary lists all failures.
* Per-file failures do not abort the whole run.

---

# Requirements

## Functional Requirements

* Copy an entire directory recursively.
* Preserve the original directory structure, including **empty directories**.
* Preserve file names.
* Preserve original files without renaming, modifying, or deleting them.
* Resume interrupted copy operations, including mid-file resume of partial copies.
* Verify every copied file using cryptographic hashes.
* Skip files already verified as successfully copied.
* Produce detailed logs.
* Support copying between different physical drives and partitions.
* Detect when the destination has changed between runs and refuse to skip stale files.

## Structural Preservation

Decisions required (with defaults):

* **Empty directories**: created; tracked in state as `dir` records (not copied files).
* **Symlinks**: by default, resolve symlinks and copy the target content (safe default). `--preserve-links` copies the symlink itself.
* **Hard links**: detected via `(st_dev, st_ino)`; a shared inode is copied once and hard-linked in the destination.
* **Special files** (FIFOs, sockets, devices): skipped with a warning unless `--preserve-special` is given (requires root for devices).
* **File metadata**: preserve permission bits and `mtime` (`shutil.copystat`). Extended attributes are optional (`--preserve-xattrs`).

---

## Non-Functional Requirements

* Implemented in **Python 3** (stdlib only).
* Able to handle datasets larger than **100 GB**.
* Efficient memory usage: hash and copy in fixed-size chunks (e.g., 1 MiB), never load whole files into memory.
* Capable of handling tens of thousands of files without O(n²) behavior.
* Minimize unnecessary disk I/O: single source read per file, no redundant re-hashing of verified files.

---

# Suggested Implementation

## Copy Workflow

For each file (in a walk of the source tree):

1. Check state for the file's `rel_path`.
2. If `verified`:
   * `os.stat` the destination file; if it exists, skip. If missing, reset to `pending`.
   * If the recorded source `size`/`mtime_ns` no longer matches the current source file, reset to `pending` and re-snapshot.
3. If `in-progress` with offset > 0 and source snapshot unchanged:
   * Open destination for append/seek, open source, stream from the recorded offset while hashing the source from byte 0 (accumulate as bytes stream past).
4. Otherwise (pending or failed):
   * Open source for reading; stream each chunk to destination **while updating the source hash accumulator** (single pass).
   * Update state to `in-progress` with the current offset periodically (every chunk) so interruption never loses more than one chunk of work.
5. After the final chunk is written and flushed (`fsync` the destination file):
   * Read the destination fully, computing its SHA-256 in chunks.
   * Compare destination hash to source hash.
6. If hashes match:
   * Verify source `size`/`mtime_ns` unchanged since snapshot; if changed, mark `failed` (stale source) unless `--force`.
   * `fsync` the destination directory so metadata is durable.
   * Mark `verified`, `COMMIT`.
7. If hashes differ:
   * Delete the destination file, reset to `pending`, increment `attempts`.
   * Retry up to `--retries`, then mark `failed`.
8. Save progress (`COMMIT`) after every completed or in-progress chunk, so no more than one chunk is lost on interruption.

## Pre-flight Checks

* Source and destination are on different filesystems when source == destination root is detected (error).
* Destination has free space ≥ source total size (checked before start and before each large file).
* State DB is created if missing; refuse to run if existing state points to a different source or destination root.
* Source directory is readable.

---

## Source File Safety

The script must treat the source directory as **read-only** throughout the entire operation.

The script **must never**:

* Delete any source file.
* Rename any source file or directory.
* Modify the contents of any source file.
* Change file metadata (timestamps, permissions, ownership, etc.).
* Move files from the source directory.

The script may only perform read operations on the source directory.

Any cleanup operation (such as removing a corrupted or partially copied file) must be performed **only on the destination directory**.

This guarantees that the original data remains completely intact regardless of interruptions, verification failures, or unexpected errors.

---

# Nice-to-Have Features

* Configurable number of retry attempts (`--retries`).
* Parallel copying of small files (e.g., < 16 MiB) via a small thread pool (`--jobs N`), ordered so large files are not starved.
* Multi-threaded hashing for a single very large file is NOT beneficial on rotating media (I/O bound) — kept as a non-goal; instead parallelize across small files.
* Exclude/include file patterns (`--exclude`, `--include`).
* Preserve file permissions and timestamps (`copystat`) — promoted to a default above.
* Dry-run mode (`--dry-run`).
* Detailed summary report after completion (files copied, skipped, failed, bytes transferred, elapsed time, per-failure list).
* Progress bar with percentage complete.
* Command-line arguments for source, destination, and options.
* `--verify-only` mode: re-verify all destination files against sources without copying.
* Checksum verification via a manifest export for offline auditing.

---

# Success Criteria

The script is considered successful if it can:

* Copy directories larger than **100 GB**.
* Resume after interruption without restarting completed work, including mid-file resume of partial files.
* Guarantee copied files are identical to the originals through hash verification.
* Leave the source directory completely unchanged.
* Work reliably when copying between different physical storage devices.
* Complete the operation without requiring manual verification of copied files.
* Lose no more than one chunk (≤ 1 MiB) of work on an interruption.

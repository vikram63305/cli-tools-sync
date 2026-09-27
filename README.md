# directory-mirror-sync

One-way directory mirror for Python: makes a destination directory match a source directory, copying changed files and removing anything no longer present in the source.

```python
from directory_mirror_sync import MirrorSync, SyncResult

result: SyncResult = MirrorSync().run("./source", "./destination")
print(result.copied, result.updated, result.deleted, result.skipped, result.bytes_copied)
```

Pass `dry_run=True` to compute counts without touching the filesystem. Pass `on_error` to decide whether filesystem errors abort the sync or are recorded in `result.errors`.

## Why

For build and deploy pipelines that need a staging tree to be an exact image of a source tree. Existing tools either rely on mtime (wrong across FAT shares, Docker bind mounts, and copy operations that reset timestamps) or pull in large dependencies. This library uses size plus a full SHA-256 to decide equality — slower than mtime but unambiguous and deterministic. The trade-off is acceptable for the working-set sizes it targets.

## Edge cases

- Symlinks to directories are not followed during the source walk, preventing cycles. Symlinked files are treated as regular files only if `os.path.isfile` agrees.
- If the destination path exists as a regular file, the sync records an error in `result.errors` and returns rather than raising.
- A missing source directory is treated as empty: no copies, no deletions. This avoids accidental mass deletion when a mount point disappears.
- File equality is by content hash, not mtime. Two files with identical timestamps but different bytes are correctly synced.

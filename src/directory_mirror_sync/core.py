from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable


_DEFAULT_HASH_CHUNK = 1 << 16  # 64 KiB: balances syscall overhead and memory.


def _hash_file(path: str, chunk_size: int = _DEFAULT_HASH_CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _walk(root: str):
    if not os.path.isdir(root):
        return
    for trip in os.walk(root):
        yield trip


@dataclass
class SyncResult:
    copied: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    errors: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.copied > 0 or self.updated > 0 or self.deleted > 0


class MirrorSync:
    def __init__(
        self,
        dry_run: bool = False,
        hash_chunk_size: int = _DEFAULT_HASH_CHUNK,
        on_error: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.hash_chunk_size = hash_chunk_size
        self._on_error = on_error if on_error is not None else self._default_on_error

    @staticmethod
    def _default_on_error(message: str, path: str) -> bool:
        return True

    def _safe(self, fn, message: str, path: str, result: SyncResult, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except OSError as exc:
            if self._on_error(f"{message}: {exc}", path):
                result.errors.append(f"{message}: {path}: {exc}")
                return None
            raise

    def _files_equal(self, src: str, dst: str) -> bool:
        try:
            if os.path.getsize(src) != os.path.getsize(dst):
                return False
        except OSError:
            return False
        try:
            return _hash_file(src, self.hash_chunk_size) == _hash_file(
                dst, self.hash_chunk_size
            )
        except OSError:
            return False

    def run(self, source: str, destination: str) -> SyncResult:
        result = SyncResult()
        src = os.path.abspath(source)
        dst = os.path.abspath(destination)

        if os.path.exists(dst) and not os.path.isdir(dst):
            msg = f"Destination exists and is not a directory: {dst}"
            if self._on_error(msg, dst):
                result.errors.append(msg)
                return result
            raise NotADirectoryError(msg)

        if not self.dry_run:
            self._safe(os.makedirs, "create destination", dst, result, dst, exist_ok=True)

        self._sync_tree(src, dst, result)
        self._prune(dst, src, result)
        return result

    def _sync_tree(self, src_root: str, dst_root: str, result: SyncResult) -> None:
        seen_dirs = {dst_root}
        for dirpath, dirnames, filenames in _walk(src_root):
            rel = os.path.relpath(dirpath, src_root)
            dst_dir = dst_root if rel == "." else os.path.join(dst_root, rel)
            seen_dirs.add(dst_dir)

            if dst_dir != dst_root and not self.dry_run:
                self._safe(os.makedirs, "create directory", dst_dir, result, dst_dir, exist_ok=True)

            for name in sorted(filenames):
                src_file = os.path.join(dirpath, name)
                dst_file = os.path.join(dst_dir, name)
                if not os.path.isfile(src_file):
                    continue
                self._sync_file(src_file, dst_file, result)

            dirnames[:] = sorted(
                d for d in dirnames
                if not os.path.islink(os.path.join(dirpath, d))
            )

    def _sync_file(self, src_file: str, dst_file: str, result: SyncResult) -> None:
        dst_exists = os.path.lexists(dst_file)
        same = dst_exists and os.path.isfile(dst_file) and self._files_equal(src_file, dst_file)

        if same:
            result.skipped += 1
            return

        size = os.path.getsize(src_file)
        if self.dry_run:
            if dst_exists:
                result.updated += 1
            else:
                result.copied += 1
            result.bytes_copied += size
            return

        self._safe(os.makedirs, "ensure parent", os.path.dirname(dst_file), result, os.path.dirname(dst_file), exist_ok=True)
        self._safe(self._copy_file, "copy file", src_file, result, src_file, dst_file, size, result)

    def _copy_file(self, src_file: str, dst_file: str, size: int, result: SyncResult) -> None:
        was_present = os.path.lexists(dst_file)
        with open(src_file, "rb") as fsrc, open(dst_file, "wb") as fdst:
            while True:
                block = fsrc.read(self.hash_chunk_size)
                if not block:
                    break
                fdst.write(block)
        if was_present:
            result.updated += 1
        else:
            result.copied += 1
        result.bytes_copied += size

    def _prune(self, dst_root: str, src_root: str, result: SyncResult) -> None:
        if not os.path.isdir(dst_root):
            return
        for dirpath, dirnames, filenames in os.walk(dst_root, topdown=False):
            rel = os.path.relpath(dirpath, dst_root)
            src_dir = src_root if rel == "." else os.path.join(src_root, rel)

            for name in filenames:
                dst_file = os.path.join(dirpath, name)
                src_file = os.path.join(src_dir, name)
                if not os.path.lexists(src_file):
                    if not self.dry_run:
                        self._safe(os.remove, "remove file", dst_file, result, dst_file)
                    result.deleted += 1

            for d in list(dirnames):
                dst_sub = os.path.join(dirpath, d)
                src_sub = os.path.join(src_dir, d)
                if not os.path.lexists(src_sub):
                    if self.dry_run:
                        result.deleted += 1
                    else:
                        ok = self._safe(self._remove_tree, "remove tree", dst_sub, result, dst_sub, result)
                        if ok is not False:
                            result.deleted += 1

    def _remove_tree(self, path: str, result: SyncResult) -> None:
        for dirpath, dirnames, filenames in os.walk(path, topdown=False):
            for name in filenames:
                fpath = os.path.join(dirpath, name)
                self._safe(os.remove, "remove file", fpath, result, fpath)
            for d in dirnames:
                dpath = os.path.join(dirpath, d)
                self._safe(os.rmdir, "remove directory", dpath, result, dpath)
        self._safe(os.rmdir, "remove directory", path, result, path)

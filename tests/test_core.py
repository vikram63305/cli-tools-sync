import os
import shutil
import tempfile
import unittest

from directory_mirror_sync import MirrorSync, SyncResult


class _TempDirs:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="dms_")
        self.src = os.path.join(self.root, "src")
        self.dst = os.path.join(self.root, "dst")
        os.makedirs(self.src)

    def clean(self):
        shutil.rmtree(self.root, ignore_errors=True)


class MirrorSyncTests(unittest.TestCase):
    def setUp(self):
        self.dirs = _TempDirs()
        self.addCleanup(self.dirs.clean)

    def _write(self, base, rel, content):
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            if isinstance(content, str):
                content = content.encode()
            f.write(content)
        return path

    def test_empty_source_creates_empty_destination(self):
        sync = MirrorSync()
        result = sync.run(self.dirs.src, self.dirs.dst)
        self.assertIsInstance(result, SyncResult)
        self.assertTrue(os.path.isdir(self.dirs.dst))
        self.assertEqual(result.copied, 0)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.deleted, 0)
        self.assertFalse(result.changed)

    def test_copies_new_files(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        self._write(self.dirs.src, "sub/b.txt", "beta")
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.copied, 2)
        with open(os.path.join(self.dirs.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "alpha")
        with open(os.path.join(self.dirs.dst, "sub", "b.txt")) as f:
            self.assertEqual(f.read(), "beta")

    def test_skips_identical_files(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        MirrorSync().run(self.dirs.src, self.dirs.dst)
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.copied, 0)
        self.assertEqual(result.updated, 0)
        self.assertFalse(result.changed)

    def test_updates_changed_file_by_content_not_mtime(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        MirrorSync().run(self.dirs.src, self.dirs.dst)
        self._write(self.dirs.src, "a.txt", "ALPHA")
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.updated, 1)
        with open(os.path.join(self.dirs.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "ALPHA")

    def test_size_difference_means_changed(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        MirrorSync().run(self.dirs.src, self.dirs.dst)
        self._write(self.dirs.src, "a.txt", "alpha-alpha")
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.updated, 1)

    def test_deletes_files_removed_from_source(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        self._write(self.dirs.src, "b.txt", "beta")
        MirrorSync().run(self.dirs.src, self.dirs.dst)
        os.remove(os.path.join(self.dirs.src, "a.txt"))
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.deleted, 1)
        self.assertFalse(os.path.exists(os.path.join(self.dirs.dst, "a.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.dirs.dst, "b.txt")))

    def test_deletes_directories_removed_from_source(self):
        self._write(self.dirs.src, "keep/x.txt", "x")
        self._write(self.dirs.src, "gone/y.txt", "y")
        self._write(self.dirs.src, "gone/sub/z.txt", "z")
        MirrorSync().run(self.dirs.src, self.dirs.dst)
        shutil.rmtree(os.path.join(self.dirs.src, "gone"))
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.deleted, 4)
        self.assertFalse(os.path.exists(os.path.join(self.dirs.dst, "gone")))
        self.assertTrue(os.path.exists(os.path.join(self.dirs.dst, "keep", "x.txt")))

    def test_dry_run_does_not_write(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        result = MirrorSync(dry_run=True).run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.copied, 1)
        self.assertEqual(result.bytes_copied, 5)
        self.assertFalse(os.path.exists(os.path.join(self.dirs.dst, "a.txt")))

    def test_dry_run_reports_deletions(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        MirrorSync().run(self.dirs.src, self.dirs.dst)
        os.remove(os.path.join(self.dirs.src, "a.txt"))
        result = MirrorSync(dry_run=True).run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.deleted, 1)
        self.assertTrue(os.path.exists(os.path.join(self.dirs.dst, "a.txt")))

    def test_destination_not_a_directory_is_reported(self):
        with open(self.dirs.dst, "w") as f:
            f.write("blocker")
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertTrue(result.errors)
        self.assertEqual(result.copied, 0)

    def test_on_error_false_propagates(self):
        self._write(self.dirs.src, "sub/a.txt", "alpha")
        os.makedirs(self.dirs.dst)
        blocker = os.path.join(self.dirs.dst, "sub")
        with open(blocker, "w") as f:
            f.write("x")
        calls = []
        def on_error(msg, path):
            calls.append((msg, path))
            return False
        with self.assertRaises(OSError):
            MirrorSync(on_error=on_error).run(self.dirs.src, self.dirs.dst)
        self.assertTrue(calls)

    def test_large_file_uses_chunked_copy(self):
        data = bytes((i * 7) % 256 for i in range(1000))
        self._write(self.dirs.src, "big.bin", data)
        result = MirrorSync(hash_chunk_size=8).run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.copied, 1)
        with open(os.path.join(self.dirs.dst, "big.bin"), "rb") as f:
            self.assertEqual(f.read(), data)

    def test_idempotent_second_run_is_noop(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        self._write(self.dirs.src, "sub/b.txt", "beta")
        first = MirrorSync().run(self.dirs.src, self.dirs.dst)
        second = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(second.skipped, 2)

    def test_bytes_copied_reflects_written_bytes(self):
        self._write(self.dirs.src, "a.txt", "alpha")
        self._write(self.dirs.src, "b.txt", "beta-beta")
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.bytes_copied, 14)

    def test_missing_source_is_noop(self):
        missing = os.path.join(self.dirs.root, "does-not-exist")
        os.makedirs(self.dirs.dst)
        result = MirrorSync().run(missing, self.dirs.dst)
        self.assertEqual(result.copied, 0)
        self.assertEqual(result.deleted, 0)

    def test_nested_destination_directory_created(self):
        self._write(self.dirs.src, "a/b/c/file.txt", "deep")
        result = MirrorSync().run(self.dirs.src, self.dirs.dst)
        self.assertEqual(result.copied, 1)
        self.assertTrue(os.path.isfile(os.path.join(self.dirs.dst, "a", "b", "c", "file.txt")))


if __name__ == "__main__":
    unittest.main()

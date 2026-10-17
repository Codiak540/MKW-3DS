import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('stage_copy', Path(__file__).resolve().parents[1] / 'tools/stage_copy.py')
copy_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(copy_module)


class StageCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.destination = self.root / 'source', self.root / 'destination'
        self.source.write_bytes(b'authored source' * 4096)

    def assert_independent(self):
        original = self.source.read_bytes()
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertNotEqual(self.source.stat().st_ino, self.destination.stat().st_ino)
        self.destination.write_bytes(b'candidate')
        self.assertEqual(self.source.read_bytes(), original)
        self.source.write_bytes(b'new source')
        self.assertEqual(self.destination.read_bytes(), b'candidate')

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS filesystem clone API')
    def test_real_clone_is_independent(self):
        # Do not silently pass this check via fallback on a non-clone filesystem.
        if not copy_module._clone_file(self.source, self.destination):
            self.skipTest('temporary filesystem does not support clonefile')
        self.assert_independent()

    def test_replacement_is_independent(self):
        self.destination.write_bytes(b'obsolete staging')
        self.assertEqual(copy_module.stage_copy(self.source, self.destination), self.destination)
        self.assert_independent()

    def test_forced_fallback_is_independent(self):
        with patch.object(copy_module, '_clone_file', return_value=False):
            copy_module.stage_copy(self.source, self.destination)
        self.assert_independent()

    def test_failed_clone_partial_is_removed_before_fallback(self):
        def failed_clone(_source, destination):
            destination.write_bytes(b'incomplete clone')
            return False
        with patch.object(copy_module, '_clone_file', side_effect=failed_clone):
            copy_module.stage_copy(self.source, self.destination)
        self.assert_independent()

    def test_same_file_aliases_never_remove_source(self):
        original = self.source.read_bytes()
        aliases = [self.source, self.root / 'link', self.root / 'hardlink']
        aliases[1].symlink_to(self.source)
        os.link(self.source, aliases[2])
        for alias in aliases:
            with self.subTest(alias=alias):
                with self.assertRaises(shutil.SameFileError):
                    copy_module.stage_copy(self.source, alias)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertTrue(alias.exists())

    def test_destination_symlink_is_replaced_without_touching_target(self):
        other = self.root / 'other'
        other.write_bytes(b'unrelated')
        self.destination.symlink_to(other)
        copy_module.stage_copy(self.source, self.destination)
        self.assertFalse(self.destination.is_symlink())
        self.assertEqual(other.read_bytes(), b'unrelated')
        self.assert_independent()

    def test_missing_source_preserves_existing_destination(self):
        self.destination.write_bytes(b'previous')
        with self.assertRaises(FileNotFoundError):
            copy_module.stage_copy(self.root / 'missing', self.destination)
        self.assertEqual(self.destination.read_bytes(), b'previous')

    def test_directory_source_or_destination_rejected(self):
        with self.assertRaises(shutil.SpecialFileError):
            copy_module.stage_copy(self.root, self.destination)
        self.destination.mkdir()
        # macOS reports EPERM for unlinking a directory; Linux reports EISDIR.
        with self.assertRaises((IsADirectoryError, PermissionError)):
            copy_module.stage_copy(self.source, self.destination)
        self.assertTrue(self.source.is_file())
        self.assertTrue(self.destination.is_dir())

    def test_copy_errors_are_propagated_and_preserve_source(self):
        original = self.source.read_bytes()
        with patch.object(copy_module, '_clone_file', return_value=False):
            with patch.object(copy_module.shutil, 'copyfile', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):
                    copy_module.stage_copy(self.source, self.destination)
        self.assertEqual(self.source.read_bytes(), original)

    def test_non_macos_uses_fallback(self):
        with patch.object(copy_module.sys, 'platform', 'linux'):
            with patch.object(copy_module.ctypes, 'CDLL', side_effect=AssertionError('must not load native API')):
                copy_module.stage_copy(self.source, self.destination)
        self.assert_independent()


if __name__ == '__main__':
    unittest.main()

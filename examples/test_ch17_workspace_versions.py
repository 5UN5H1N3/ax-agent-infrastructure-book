import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ch17_workspace_versions import Binding, Workspace, acceptance, demo, marker_name, preflight, setup_inline


class WorkspaceVersionTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace('team-a', 'code', (('version.txt', 'v1\n'),))
        self.store = {('team-a', 'code'): self.ws, ('team-a', 'extra'): self.ws}

    def test_existing_setup_is_skipped(self):
        self.assertEqual(demo()['existing_setup'], 'SKIPPED')

    def test_existing_version_not_replaced(self):
        self.assertEqual(demo()['existing_file'], 'v1')

    def test_drift_rejected(self):
        self.assertEqual(demo()['existing_acceptance_for_v2'], 'ENVIRONMENT_DRIFT')

    def test_notes_preserved(self):
        self.assertTrue(demo()['existing_notes_preserved'])

    def test_new_task_uses_new_version(self):
        self.assertEqual(demo()['new_task_file'], 'v2')

    def test_new_task_accepted(self):
        self.assertEqual(demo()['new_task_acceptance'], 'ACCEPT')

    def test_missing_reference_rejected_before_launch(self):
        self.assertEqual(demo()['missing_reference_preflight'], ['MISSING_WORKSPACE'])

    def test_missing_reference_can_finish_empty_toy_setup(self):
        self.assertEqual(demo()['missing_reference_unchecked_setup'], 'SETUP_COMPLETE')

    def test_demo_does_not_contact_services(self):
        self.assertFalse(demo()['ax_and_substrate_contacted'])

    def test_demo_deterministic(self):
        self.assertEqual(demo(), demo())

    def test_valid_binding(self):
        self.assertEqual(preflight('team-a', (Binding('code', '/workspace/service'),), self.store), [])

    def test_default_binding_path(self):
        self.assertEqual(preflight('team-a', (Binding('code', ''),), self.store), [])

    def test_namespace_is_part_of_lookup(self):
        self.assertIn('MISSING_WORKSPACE', preflight('other', (Binding('code', '/workspace/code'),), self.store))

    def test_duplicate_name(self):
        self.assertIn('DUPLICATE_BINDING', preflight('team-a', (Binding('code', '/workspace/a'), Binding('code', '/workspace/b')), self.store))

    def test_duplicate_path(self):
        self.assertIn('OVERLAPPING_PATHS', preflight('team-a', (Binding('code', '/workspace/a'), Binding('extra', '/workspace/a')), self.store))

    def test_nested_paths(self):
        for paths in [('/workspace/a', '/workspace/a/b'), ('/workspace/a/b', '/workspace/a')]:
            with self.subTest(paths=paths):
                self.assertIn('OVERLAPPING_PATHS', preflight('team-a', tuple(Binding(n, p) for n, p in zip(['code', 'extra'], paths)), self.store))

    def test_noncanonical_paths(self):
        for path in ['workspace/code', '/workspace/a/../code', '/workspace/code/', '/workspace/./code', '/workspace//code', '/etc/code', '/workspace', '/workspace-other/code']:
            with self.subTest(path=path):
                self.assertIn('UNSAFE_PATH', preflight('team-a', (Binding('code', path),), self.store))

    def test_marker_collision(self):
        self.assertEqual(marker_name('/workspace/a-b'), marker_name('/workspace/a/b'))
        self.assertIn('MARKER_COLLISION', preflight('team-a', (Binding('code', '/workspace/a-b'), Binding('extra', '/workspace/a/b')), self.store))

    def test_missing_file(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(acceptance(Path(tmp), self.ws), 'ENVIRONMENT_DRIFT')

    def test_acceptance_does_not_write(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            setup_inline(root, '/workspace/code', self.ws)
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(acceptance(root / 'workspace/code', self.ws), 'ACCEPT')
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_each_task_has_own_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ['a', 'b']:
                (root / name).mkdir()
                setup_inline(root / name, '/workspace/code', self.ws)
            (root / 'a/workspace/code/version.txt').write_text('agent edit')
            self.assertEqual(acceptance(root / 'b/workspace/code', self.ws), 'ACCEPT')

    def test_toy_files_reject_escape(self):
        for name in ['', '.', '/tmp/escape', '../escape', 'a/../../escape']:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    Workspace('team-a', 'code', ((name, 'x'),))

    def test_toy_files_reject_duplicates(self):
        with self.assertRaises(ValueError):
            Workspace('team-a', 'code', (('a', 'x'), ('./a', 'y')))

    def test_toy_setup_rejects_escape(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                setup_inline(Path(tmp), '/workspace/../../etc', self.ws)


if __name__ == '__main__':
    unittest.main()

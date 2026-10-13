"""Release guard tests: all GitHub/gh calls are mocked; nothing is published."""

import importlib.util
import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_release', ROOT / 'scripts/windows_release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = 'a' * 40
OTHER_SHA = 'b' * 40


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'installer').mkdir()
        (self.root / 'docs/releases').mkdir(parents=True)
        (self.root / 'dist').mkdir()
        self.env = {
            'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_REPOSITORY': release.REPOSITORY,
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': SHA,
            'RELEASE_TAG': 'v0.1.2', 'RELEASE_CONFIRM': 'true',
        }
        self.responses = {
            'git/ref/tags/v0.1.2': {'object': {'type': 'commit', 'sha': SHA}},
            'releases/tags/v0.1.2': None,
            'releases/latest': {'tag_name': 'v0.1.1', 'draft': False, 'prerelease': False},
        }
        self.set_version('0.1.2')
        api_patch = patch.object(release, 'api_get', side_effect=lambda path, **kw: self.responses[path])
        self.api = api_patch.start()
        self.addCleanup(api_patch.stop)
        run_patch = patch.object(release.subprocess, 'run', return_value=SimpleNamespace(stdout=SHA + '\n'))
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)

    def set_version(self, version):
        (self.root / 'installer/hellgato.iss').write_text(f'#define AppVersion "{version}"\n', encoding='utf-8')
        (self.root / f'docs/releases/{version}.md').write_text(f'# Hellgato {version}\n\nChanges.\n', encoding='utf-8')
        self.installer = self.root / f'dist/Hellgato-{version}-Setup.exe'
        self.installer.write_bytes(b'unit-test installer fixture, not an executable')
        self.checksum = self.installer.with_name(self.installer.name + '.sha256')
        digest = release.hashlib.sha256(self.installer.read_bytes()).hexdigest()
        self.checksum.write_text(f'{digest}  {self.installer.name}\n', encoding='ascii')

    def check(self, **overrides):
        return release.check_release(self.root, {**self.env, **overrides})

    def assert_no_publish(self):
        self.assertFalse(any(call.args[0][0] == 'gh' for call in self.run.call_args_list))

    def test_prepared_release_passes(self):
        self.assertEqual(self.check(), ('v0.1.2', '0.1.2'))
        self.api.assert_any_call('releases/tags/v0.1.2', allow_missing=True)
        self.assert_no_publish()

    def test_automatic_events_forks_other_refs_and_missing_confirmation_are_rejected(self):
        cases = [
            ('GITHUB_EVENT_NAME', event) for event in
            ('push', 'pull_request', 'pull_request_target', 'workflow_run', 'release', '')
        ] + [
            ('GITHUB_REPOSITORY', 'someone/hellgato'),
            ('GITHUB_REF', 'refs/heads/feature'), ('GITHUB_REF', 'refs/tags/v0.1.2'),
            ('RELEASE_CONFIRM', 'false'), ('RELEASE_CONFIRM', ''), ('RELEASE_CONFIRM', 'True'),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    self.check(**{key: value})
                self.api.assert_not_called()
        self.assert_no_publish()

    def test_only_canonical_stable_tags_are_accepted(self):
        for tag in ('', '0.1.2', 'v0.1', 'v01.1.2', 'v1.02.3', 'v1.2.03',
                    'v1.2.3-rc.1', 'v1.2.3+build', 'v1.2.3\n', ' v1.2.3',
                    '../v1.2.3', 'v1.2.3; echo bad', 'v1.2.3/other'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.check(RELEASE_TAG=tag)
        self.api.assert_not_called()

    def test_installer_version_is_the_windows_source_of_truth(self):
        self.assertEqual(release.installer_version(self.root), '0.1.2')
        for source in ('', '#define AppVersion "0.1.2-rc.1"\n',
                       '#define AppVersion "0.1.2"\n#define AppVersion "0.1.3"\n'):
            with self.subTest(source=source):
                (self.root / 'installer/hellgato.iss').write_text(source, encoding='utf-8')
                with self.assertRaises(ValueError):
                    release.installer_version(self.root)

    def test_tag_and_installer_version_must_match(self):
        with self.assertRaisesRegex(ValueError, 'AppVersion'):
            self.check(RELEASE_TAG='v0.1.3')
        self.api.assert_not_called()

    def test_missing_or_empty_release_notes_are_rejected(self):
        notes = self.root / 'docs/releases/0.1.2.md'
        notes.unlink()
        with self.assertRaisesRegex(ValueError, 'release notes'):
            self.check()
        notes.write_text(' \n\t', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'release notes'):
            self.check()
        self.api.assert_not_called()

    def test_missing_or_short_workflow_sha_is_rejected(self):
        for sha in ('', SHA[:7], 'not-a-sha'):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                self.check(GITHUB_SHA=sha)
        self.api.assert_not_called()

    def test_wrong_checkout_is_rejected(self):
        self.run.return_value.stdout = OTHER_SHA
        with self.assertRaisesRegex(ValueError, 'must match'):
            self.check()
        self.api.assert_not_called()

    def test_wrong_remote_tag_commit_is_rejected(self):
        self.responses['git/ref/tags/v0.1.2']['object']['sha'] = OTHER_SHA
        with self.assertRaisesRegex(ValueError, 'must match'):
            self.check()

    def test_annotated_tag_is_peeled_to_a_commit(self):
        self.responses['git/ref/tags/v0.1.2']['object'] = {'type': 'tag', 'sha': OTHER_SHA}
        self.responses[f'git/tags/{OTHER_SHA}'] = {'object': {'type': 'commit', 'sha': SHA}}
        self.assertEqual(self.check(), ('v0.1.2', '0.1.2'))
        self.api.assert_any_call(f'git/tags/{OTHER_SHA}')

    def test_tag_cycles_and_non_commit_targets_are_rejected(self):
        self.responses['git/ref/tags/v0.1.2']['object'] = {'type': 'tag', 'sha': OTHER_SHA}
        self.responses[f'git/tags/{OTHER_SHA}'] = {'object': {'type': 'tag', 'sha': OTHER_SHA}}
        with self.assertRaisesRegex(ValueError, 'tag chain'):
            self.check()
        self.responses['git/ref/tags/v0.1.2']['object']['type'] = 'tree'
        with self.assertRaisesRegex(ValueError, 'point to a commit'):
            self.check()

    def test_missing_remote_tag_or_lookup_failure_stops_publication(self):
        self.api.side_effect = ValueError('GitHub lookup failed')
        with self.assertRaisesRegex(ValueError, 'lookup failed'):
            self.check()
        self.api.assert_called_once_with('git/ref/tags/v0.1.2')
        self.assert_no_publish()

    def test_existing_release_including_draft_or_prerelease_is_rejected(self):
        for state in ({'draft': False}, {'draft': True}, {'prerelease': True}):
            with self.subTest(state=state):
                self.responses['releases/tags/v0.1.2'] = state
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    self.check()
        self.assert_no_publish()

    def test_equal_or_lower_version_is_rejected(self):
        for tag in ('v0.1.2', 'v0.2.0', 'v1.0.0'):
            with self.subTest(tag=tag):
                self.responses['releases/latest']['tag_name'] = tag
                with self.assertRaisesRegex(ValueError, 'greater than'):
                    self.check()

    def test_version_comparison_is_numeric(self):
        self.set_version('0.1.10')
        self.responses['git/ref/tags/v0.1.10'] = self.responses['git/ref/tags/v0.1.2']
        self.responses['releases/tags/v0.1.10'] = None
        self.responses['releases/latest']['tag_name'] = 'v0.1.9'
        self.assertEqual(self.check(RELEASE_TAG='v0.1.10'), ('v0.1.10', '0.1.10'))

    def test_first_stable_release_can_pass(self):
        self.responses['releases/latest'] = None
        self.assertEqual(self.check(), ('v0.1.2', '0.1.2'))

    def test_unexpected_latest_release_is_rejected(self):
        for latest in ({}, {'tag_name': 'v1.0.0-rc.1', 'draft': False, 'prerelease': False},
                       {'tag_name': 'v0.1.1', 'draft': True, 'prerelease': False}):
            with self.subTest(latest=latest):
                self.responses['releases/latest'] = latest
                with self.assertRaises(ValueError):
                    self.check()

    def test_valid_artifacts_pass_with_windows_line_endings(self):
        self.checksum.write_bytes(self.checksum.read_bytes().replace(b'\n', b'\r\n'))
        self.assertEqual(release.check_artifacts(self.root, '0.1.2'), (self.installer, self.checksum))

    def test_missing_empty_or_wrong_version_installer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'artifact'):
            release.check_artifacts(self.root, '0.1.3')
        self.installer.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'artifact'):
            release.check_artifacts(self.root, '0.1.2')
        self.installer.unlink()
        with self.assertRaisesRegex(ValueError, 'artifact'):
            release.check_artifacts(self.root, '0.1.2')

    def test_missing_checksum_is_rejected(self):
        self.checksum.unlink()
        with self.assertRaisesRegex(ValueError, 'artifact'):
            release.check_artifacts(self.root, '0.1.2')

    def test_wrong_checksum_name_format_and_hash_are_rejected(self):
        valid = self.checksum.read_text(encoding='ascii')
        for value in (valid.replace('0.1.2', '0.1.1'), 'not a checksum',
                      '0' * 64 + '  ' + self.installer.name, valid + valid):
            with self.subTest(value=value):
                self.checksum.write_text(value, encoding='ascii')
                with self.assertRaises(ValueError):
                    release.check_artifacts(self.root, '0.1.2')

    def test_publish_rechecks_existing_release_after_build(self):
        self.check()
        self.responses['releases/tags/v0.1.2'] = {'draft': False}
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(ValueError, 'already exists'):
                release.publish_release(self.root)
        self.assert_no_publish()

    def test_publish_rechecks_moved_tag_after_build(self):
        self.check()
        self.responses['git/ref/tags/v0.1.2']['object']['sha'] = OTHER_SHA
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(ValueError, 'must match'):
                release.publish_release(self.root)
        self.assert_no_publish()

    def test_publish_refuses_tampered_installer(self):
        self.installer.write_bytes(b'tampered fixture')
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                release.publish_release(self.root)
        self.assert_no_publish()

    def test_publish_command_uses_verified_tag_exact_files_and_commit(self):
        with patch.dict(os.environ, self.env, clear=True):
            release.publish_release(self.root)
        args = self.run.call_args.args[0]
        self.assertEqual(args[:6], ['gh', 'release', 'create', 'v0.1.2', str(self.installer), str(self.checksum)])
        self.assertIn('--verify-tag', args)
        self.assertIn('--fail-on-no-commits', args)
        self.assertEqual(args[args.index('--target') + 1], SHA)
        self.assertEqual(args[args.index('--notes-file') + 1], str(self.root / 'docs/releases/0.1.2.md'))
        for forbidden in ('--clobber', '--draft', '--prerelease', 'delete', 'upload', 'edit'):
            self.assertNotIn(forbidden, args)
        self.assertTrue(self.run.call_args.kwargs['check'])
        self.assertNotIn('shell', self.run.call_args.kwargs)


class ApiChecks(unittest.TestCase):
    def test_only_explicitly_allowed_404_is_treated_as_missing(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            for status in (401, 403, 404, 429, 500, 503):
                error = urllib.error.HTTPError('https://api.github.com', status, 'fixture', {}, None)
                with self.subTest(status=status), patch.object(release.urllib.request, 'urlopen', side_effect=error):
                    with self.assertRaises(ValueError):
                        release.api_get('git/ref/tags/v0.1.2')
                    if status == 404:
                        self.assertIsNone(release.api_get('releases/tags/v0.1.2', allow_missing=True))
                    else:
                        with self.assertRaises(ValueError):
                            release.api_get('releases/tags/v0.1.2', allow_missing=True)

    def test_network_errors_fail_closed(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            with patch.object(release.urllib.request, 'urlopen', side_effect=urllib.error.URLError('offline')):
                with self.assertRaises(ValueError):
                    release.api_get('releases/tags/v0.1.2', allow_missing=True)

    def test_malformed_api_responses_are_not_absence(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            for content in (b'null', b'[]', b'not json'):
                with self.subTest(content=content):
                    with patch.object(release.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
                        with self.assertRaises(ValueError):
                            release.api_get('releases/tags/v0.1.2', allow_missing=True)

    def test_json_object_is_returned(self):
        expected = {'tag_name': 'v0.1.1'}
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            with patch.object(release.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(expected).encode())):
                self.assertEqual(release.api_get('releases/latest'), expected)

    def test_missing_token_is_rejected_without_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(release.urllib.request, 'urlopen') as urlopen:
            with self.assertRaisesRegex(ValueError, 'GH_TOKEN'):
                release.api_get('releases/latest')
            urlopen.assert_not_called()


class WorkflowContractChecks(unittest.TestCase):
    def test_release_is_manual_only_and_confirmed_main_only(self):
        text = (ROOT / '.github/workflows/windows-release.yml').read_text(encoding='utf-8')
        events = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(re.findall(r'^  ([a-z_]+):', events, re.MULTILINE), ['workflow_dispatch'])
        self.assertIn('default: false', events)
        guard = "github.repository == 'restarea92/hellgato' && github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && inputs.confirm"
        self.assertEqual(text.count('if: ' + guard), 2)
        self.assertIn('needs: [preflight, build]', text)
        self.assertIn('uses: ./.github/workflows/windows-build.yml', text)
        self.assertEqual(text.count('contents: write'), 1)
        self.assertIn('group: windows-release\n  cancel-in-progress: false', text)
        self.assertIn('python scripts/windows_release.py check', text)
        self.assertIn('python scripts/windows_release.py publish', text)
        self.assertNotIn('v0.1.1', text)

    def test_build_is_read_only_and_preserves_existing_checks(self):
        text = (ROOT / '.github/workflows/windows-build.yml').read_text(encoding='utf-8')
        for event in ('push', 'pull_request', 'workflow_dispatch', 'workflow_call'):
            self.assertIn('\n  ' + event + ':', text)
        self.assertEqual(text.count("'native/**'"), 2)
        self.assertIn('contents: read', text)
        self.assertNotIn('contents: write', text)
        self.assertNotIn('GH_TOKEN', text)
        self.assertNotIn('windows_release.py publish', text)
        for command in ("python -m unittest discover -s test -p 'test_*.py'",
                        '-File test/test_installer_migration.ps1', './scripts/build-windows.ps1 -Iscc',
                        './work/build-runtime/node.exe --test test/*.test.mjs',
                        'python scripts/windows_release.py artifacts'):
            self.assertIn(command, text)
        self.assertIn('if-no-files-found: error', text)
        self.assertIn('Setup.exe.sha256', text)

    def test_build_and_publish_share_only_the_current_attempt_artifact(self):
        name = 'name: windows-installer-${{ github.run_id }}-${{ github.run_attempt }}'
        for workflow in ('windows-build.yml', 'windows-release.yml'):
            text = (ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8')
            self.assertIn(name, text)
            self.assertIn('ref: ${{ github.sha }}', text)
            self.assertNotIn('persist-credentials: true', text)

    def test_build_script_derives_filename_without_changing_hash_generation(self):
        text = (ROOT / 'scripts/build-windows.ps1').read_text(encoding='utf-8')
        self.assertIn('$version = & $Python scripts/windows_release.py version', text)
        self.assertIn('Get-Item "dist/Hellgato-$version-Setup.exe"', text)
        self.assertIn('Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256', text)
        self.assertIn('"$checksum  $($installer.Name)"', text)
        self.assertNotIn('Hellgato-0.1.1-Setup.exe', text)


if __name__ == '__main__':
    unittest.main()

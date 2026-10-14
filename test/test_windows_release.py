"""Release safety tests. GitHub, gh, and builds are mocked; nothing is published."""

import ast
import copy
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_release', ROOT / 'scripts/windows_release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA, BASE, HEAD, TREE = (letter * 40 for letter in 'abcd')


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('installer', 'app', 'packages/windows-release', 'docs/releases', 'dist', '.release', 'scripts'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ('installer/requirements-build.txt', 'scripts/prepare-windows.py', 'scripts/prepare-cora.mjs'):
            (self.root / name).write_bytes((ROOT / name).read_bytes())
        self.env = {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REPOSITORY': release.REPOSITORY,
                    'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': SHA, 'RELEASE_SHA': SHA,
                    'RELEASE_ENABLED': 'true', 'RELEASE_APPROVAL_FILE': '.release/approval.json',
                    'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '2',
                    'WINDOWS_BUILD_RUN_ID': '100', 'WINDOWS_BUILD_RUN_ATTEMPT': '2', 'WINDOWS_ARTIFACT_ID': '55'}
        self.tags, self.releases, self.bytes = {}, {}, {}
        self.next_id = 10
        self.responses = {
            f'compare/{SHA}...main': {'status': 'identical', 'merge_base_commit': {'sha': SHA}},
            f'compare/{SHA}...{SHA}': {'status': 'identical', 'merge_base_commit': {'sha': SHA}},
            'pulls/7': {'number': 7, 'merged': True, 'state': 'closed', 'merge_commit_sha': SHA,
                        'base': {'ref': 'main', 'repo': {'full_name': release.REPOSITORY}},
                        'head': {'ref': 'release/bootstrap-windows', 'sha': HEAD, 'repo': {'full_name': release.REPOSITORY}},
                        'user': {'login': 'restarea92'}, 'merged_by': {'login': 'restarea92'}},
            f'commits/{SHA}': {'sha': SHA, 'parents': [{'sha': BASE}], 'commit': {'tree': {'sha': TREE}}},
            f'git/commits/{HEAD}': {'tree': {'sha': TREE}},
            'actions/artifacts/55': {'id': 55, 'name': 'windows-installer-100-2', 'expired': False,
                                     'digest': 'sha256:' + 'e' * 64,
                                     'workflow_run': {'id': 100, 'head_sha': SHA, 'head_branch': 'main'}},
            'actions/runs/100/attempts/2': {'id': 100, 'run_attempt': 2, 'repository': {'full_name': release.REPOSITORY},
                                          'head_repository': {'full_name': release.REPOSITORY}, 'head_sha': SHA,
                                          'head_branch': 'main', 'event': 'push', 'path': '.github/workflows/release.yml',
                                          'status': 'in_progress', 'conclusion': None},
            'actions/runs/100/attempts/2/jobs?per_page=100&page=1': {'total_count': 1, 'jobs': [
                {'id': 77, 'name': 'windows-build / build', 'status': 'completed', 'conclusion': 'success'}]},
            'actions/runs/100/attempts/2/jobs?per_page=100&page=2': {'total_count': 1, 'jobs': []},
        }
        self.set_version('0.1.2')
        self.add_legacy('0.1.1')
        api_patch = patch.object(release, 'api_get', side_effect=self.api_response)
        self.api = api_patch.start(); self.addCleanup(api_patch.stop)
        run_patch = patch.object(release.subprocess, 'run', side_effect=self.command)
        self.run = run_patch.start(); self.addCleanup(run_patch.stop)
        bytes_patch = patch.object(release, 'asset_bytes', side_effect=lambda asset: self.bytes[asset['id']])
        self.asset_read = bytes_patch.start(); self.addCleanup(bytes_patch.stop)
        self.write_plan()
        self.manifest = release.build_manifest(self.root, self.env)
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            for path in (self.installer, self.checksum, self.root / 'dist/build-manifest.json'):
                archive.writestr(path.name, path.read_bytes())
        self.archive = content.getvalue()
        self.responses['actions/artifacts/55']['digest'] = 'sha256:' + release.sha256(self.archive)
        release._VERIFIED_ARCHIVES.clear()
        archive_patch = patch.object(release, 'artifact_archive_bytes', side_effect=lambda identifier: self.archive)
        self.archive_read = archive_patch.start(); self.addCleanup(archive_patch.stop)
        self.run.reset_mock(); self.api.reset_mock()

    def set_version(self, version):
        self.version = version
        (self.root / 'packages/windows-release/package.json').write_text(json.dumps(
            {'name': '@hellgato/windows-release', 'private': True, 'version': version}))
        (self.root / 'installer/hellgato.iss').write_text('#include "version.iss"\n[Setup]\nAppVersion={#AppVersion}\n')
        (self.root / 'installer/version.iss').write_text(f'#define AppVersion "{version}"\n')
        (self.root / 'app/release.json').write_text(json.dumps({'version': version, 'channel': 'stable'}))
        (self.root / f'docs/releases/{version}.md').write_text(f'# Hellgato {version}\n\nChanges.\n')
        self.installer = self.root / f'dist/Hellgato-{version}-Setup.exe'
        self.installer.write_bytes(b'unit test installer, not executable')
        self.checksum = self.installer.with_name(self.installer.name + '.sha256')
        self.checksum.write_text(f'{release.sha256(self.installer.read_bytes())}  {self.installer.name}\n')

    def write_plan(self, *, bootstrap=True, old='0.1.1'):
        self.plan = {'schemaVersion': 1, 'repository': release.REPOSITORY, 'baseSha': BASE, 'bootstrap': bootstrap,
                     'components': [{'component': 'windows', 'oldVersion': old, 'version': self.version,
                                     'channel': 'stable', 'tag': 'v' + self.version, 'notes': f'docs/releases/{self.version}.md'}]}
        path = self.root / '.release/plan.json'; path.write_bytes(release.json_bytes(self.plan))
        self.approval = {'schemaVersion': 1, 'repository': release.REPOSITORY, 'releaseSha': SHA,
                         'planSha256': release.sha256(path.read_bytes()), 'baseSha': BASE,
                         'bootstrap': bootstrap, 'components': self.plan['components'],
                         'pullRequest': {'number': 7, 'headSha': HEAD, 'mergedBy': 'restarea92'}}
        (self.root / '.release/approval.json').write_bytes(release.json_bytes(self.approval))
        if not bootstrap:
            self.responses['pulls/7']['head']['ref'] = 'changeset-release/main'
            self.responses['pulls/7']['user']['login'] = 'github-actions[bot]'

    def add_asset(self, item, name, data):
        self.next_id += 1
        asset = {'id': self.next_id, 'name': name, 'state': 'uploaded', 'size': len(data)}
        item.setdefault('assets', []).append(asset)
        self.bytes[asset['id']] = data
        return asset

    def add_legacy(self, version):
        self.next_id += 1
        tag = 'v' + version
        item = {'id': self.next_id, 'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': []}
        name, data = f'Hellgato-{version}-Setup.exe', b'legacy installer fixture'
        self.add_asset(item, name, data)
        self.add_asset(item, name + '.sha256', f'{release.sha256(data)}  {name}\n'.encode())
        self.releases[tag] = item
        return item

    def public_manifest(self):
        return {**self.manifest, 'artifact': {'id': 55, 'name': 'windows-installer-100-2', 'sha256': self.responses['actions/artifacts/55']['digest'][7:]}}

    def add_ours(self, *, draft=False, missing=()):
        self.next_id += 1
        manifest = self.public_manifest()
        item = {'id': self.next_id, 'tag_name': 'v' + self.version, 'draft': draft, 'prerelease': False,
                'name': f'Hellgato Stream Dog {self.version}', 'body': release.release_body(self.root, manifest),
                'author': {'login': 'github-actions[bot]', 'type': 'Bot'}, 'assets': []}
        self.releases[item['tag_name']] = item
        self.tags[item['tag_name']] = {'object': {'type': 'commit', 'sha': SHA}}
        contents = {self.installer.name: self.installer.read_bytes(), self.checksum.name: self.checksum.read_bytes(),
                    release.MANIFEST_NAME: release.json_bytes(manifest)}
        for name, data in contents.items():
            if name not in missing:
                self.add_asset(item, name, data)
        return item

    def api_response(self, path, **kwargs):
        if path in self.responses:
            return self.responses[path]
        if path.startswith('git/ref/tags/'):
            value = self.tags.get(path.removeprefix('git/ref/tags/'))
            if value is None and not kwargs.get('allow_missing'):
                raise ValueError('GitHub lookup failed: missing tag')
            return value
        if path.startswith('releases/tags/'):
            return self.releases.get(path.removeprefix('releases/tags/'))
        if path.startswith('releases?'):
            page = int(path.rsplit('=', 1)[1]); values = list(self.releases.values())
            return values[(page - 1) * 100:page * 100]
        match = re.fullmatch(r'releases/(\d+)/assets\?per_page=100&page=(\d+)', path)
        if match:
            item = next(item for item in self.releases.values() if item['id'] == int(match[1]))
            page = int(match[2]); return item['assets'][(page - 1) * 100:page * 100]
        raise AssertionError('Unexpected API path: ' + path)

    def command(self, args, **kwargs):
        if args == ['git', 'rev-parse', 'HEAD']:
            return SimpleNamespace(stdout=SHA + '\n')
        if args[:3] == ['gh', 'api', '--method']:
            tag = next(arg.removeprefix('ref=refs/tags/') for arg in args if arg.startswith('ref=refs/tags/'))
            self.tags[tag] = {'object': {'type': 'commit', 'sha': SHA}}
        elif args[:3] == ['gh', 'release', 'create']:
            self.add_ours(draft=True, missing=(self.installer.name, self.checksum.name, release.MANIFEST_NAME))
        elif args[:3] == ['gh', 'release', 'upload']:
            path = Path(args[4]); self.add_asset(self.releases[args[3]], path.name, path.read_bytes())
        elif args[:3] == ['gh', 'release', 'edit']:
            self.releases[args[3]]['draft'] = False
        else:
            raise AssertionError('Unexpected command: ' + repr(args))
        return SimpleNamespace(stdout='')

    def check(self, **overrides):
        return release.check_release(self.root, {**self.env, **overrides})

    def assert_no_publish(self):
        self.assertFalse(any(call.args[0][0] == 'gh' for call in self.run.call_args_list))

    def test_prepared_candidate_preflight_does_not_publish_or_create_tag(self):
        self.assertEqual(self.check(), ('v0.1.2', '0.1.2'))
        self.assert_no_publish(); self.assertNotIn('v0.1.2', self.tags)

    def test_disallowed_events_forks_refs_and_disabled_publication_are_rejected(self):
        cases = [('GITHUB_EVENT_NAME', value) for value in ('pull_request', 'pull_request_target', 'workflow_run', 'release', '')]
        cases += [('GITHUB_REPOSITORY', 'someone/hellgato'), ('GITHUB_REF', 'refs/heads/feature'),
                  ('GITHUB_REF', 'refs/tags/v0.1.2'), ('RELEASE_ENABLED', ''), ('RELEASE_ENABLED', 'True')]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.check(**{key: value})
        self.api.assert_not_called(); self.assert_no_publish()

    def test_read_only_verification_can_run_before_publication_enabled(self):
        env = {**self.env, 'RELEASE_ENABLED': ''}
        self.assertEqual(release.check_release(self.root, env, require_enabled=False), ('v0.1.2', '0.1.2'))
        approval, _ = release.approval_context(self.root, env, require_enabled=False)
        release.verify_build(self.root, approval, env)
        with self.assertRaises(ValueError): release.publish_release(self.root, env)
        self.assert_no_publish()

    def test_old_manual_confirm_tag_path_cannot_bypass_shared_approval(self):
        with self.assertRaises(ValueError):
            release.check_release(self.root, {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REPOSITORY': release.REPOSITORY,
                                               'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': SHA,
                                               'RELEASE_CONFIRM': 'true', 'RELEASE_TAG': 'v0.1.2'})
        self.api.assert_not_called()

    def test_only_canonical_stable_tags_are_accepted(self):
        for tag in ('', '0.1.2', 'v0.1', 'v01.1.2', 'v1.02.3', 'v1.2.03', 'v1.2.3-rc.1',
                    'v1.2.3+build', 'v1.2.3\n', ' v1.2.3', '../v1.2.3', 'v1.2.3; echo bad', 'v1.2.3/other'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.stable_version(tag)

    def test_single_source_matches_inno_and_runtime(self):
        self.assertEqual(release.installer_version(self.root), '0.1.2')
        for path, source in [('installer/version.iss', '#define AppVersion "0.1.1"\n'),
                             ('app/release.json', '{"version":"0.1.1","channel":"stable"}'),
                             ('installer/hellgato.iss', '#define AppVersion "0.1.2"\n')]:
            target = self.root / path; original = target.read_bytes(); target.write_text(source)
            with self.subTest(path=path), self.assertRaises(ValueError):
                release.installer_version(self.root)
            target.write_bytes(original)

    def test_duplicate_inno_definitions_are_rejected(self):
        target = self.root / 'installer/version.iss'; target.write_text(target.read_text() * 2)
        with self.assertRaises(ValueError): release.installer_version(self.root)

    def test_missing_or_empty_release_notes_are_rejected(self):
        notes = self.root / 'docs/releases/0.1.2.md'; notes.unlink()
        with self.assertRaisesRegex(ValueError, 'release notes'): self.check()
        notes.write_text(' \n\t')
        with self.assertRaisesRegex(ValueError, 'release notes'): self.check()
        self.api.assert_not_called()

    def test_missing_short_sha_or_wrong_checkout_rejected(self):
        for sha in ('', SHA[:7], 'not-a-sha'):
            with self.subTest(sha=sha), self.assertRaises(ValueError): self.check(RELEASE_SHA=sha)
        self.run.side_effect = None; self.run.return_value = SimpleNamespace(stdout=BASE)
        with self.assertRaisesRegex(ValueError, 'must match'): self.check()
        self.api.assert_not_called()

    def test_forged_approval_stale_plan_foreign_component_rejected(self):
        for key, value in [('releaseSha', BASE), ('planSha256', '0' * 64), ('components', []), ('bootstrap', False)]:
            approval = {**self.approval, key: value}
            (self.root / '.release/approval.json').write_bytes(release.json_bytes(approval))
            with self.subTest(key=key), self.assertRaises(ValueError): self.check()
        self.api.assert_not_called()

    def test_unmerged_wrong_author_wrong_base_and_different_tree_are_rejected(self):
        mutations = [('pulls/7', 'merged', False), ('pulls/7', 'merge_commit_sha', BASE),
                     ('pulls/7', 'merged_by', {'login': 'other'}), ('pulls/7', 'user', {'login': 'other'}),
                     (f'commits/{SHA}', 'parents', [{'sha': HEAD}]), (f'git/commits/{HEAD}', 'tree', {'sha': BASE})]
        for path, key, value in mutations:
            previous = self.responses[path][key]; self.responses[path][key] = value
            with self.subTest(path=path, key=key), self.assertRaises(ValueError): self.check()
            self.responses[path][key] = previous
        self.assert_no_publish()

    def test_release_sha_must_be_on_main(self):
        self.responses[f'compare/{SHA}...main']['status'] = 'diverged'
        with self.assertRaisesRegex(ValueError, 'main history'): self.check()

    def test_tag_commit_must_match_and_annotated_tags_are_peeled(self):
        self.tags['v0.1.2'] = {'object': {'type': 'commit', 'sha': BASE}}
        with self.assertRaisesRegex(ValueError, 'must match'): self.check()
        self.tags['v0.1.2'] = {'object': {'type': 'tag', 'sha': BASE}}
        self.responses[f'git/tags/{BASE}'] = {'object': {'type': 'commit', 'sha': SHA}}
        self.assertEqual(self.check(), ('v0.1.2', '0.1.2'))

    def test_tag_cycles_noncommits_and_lookup_failures_stop_publication(self):
        self.tags['v0.1.2'] = {'object': {'type': 'tag', 'sha': BASE}}
        self.responses[f'git/tags/{BASE}'] = {'object': {'type': 'tag', 'sha': BASE}}
        with self.assertRaisesRegex(ValueError, 'tag chain'): self.check()
        self.tags['v0.1.2']['object']['type'] = 'tree'
        with self.assertRaisesRegex(ValueError, 'point to a commit'): self.check()
        self.api.side_effect = ValueError('GitHub lookup failed')
        with self.assertRaisesRegex(ValueError, 'lookup failed'): self.check()
        self.assert_no_publish()

    def test_resolver_ignores_npm_drafts_prerelease_and_reads_all_pages(self):
        legacy = self.releases['v0.1.1']
        first = [{'id': 1000 + i, 'tag_name': 'hellgato@9.9.' + str(i), 'draft': False, 'prerelease': False} for i in range(100)]
        first[0].update(tag_name='v99.0.0', draft=True)
        first[1].update(tag_name='v98.0.0', prerelease=True)
        self.responses['releases?per_page=100&page=1'] = first
        self.responses['releases?per_page=100&page=2'] = [legacy]
        self.responses['releases?per_page=100&page=3'] = []
        result = release.resolve_windows_stable()
        self.assertEqual(result['latest']['version'], '0.1.1')
        self.api.assert_any_call('releases?per_page=100&page=3', array=True)
        self.assertFalse(any(call.args[0] == 'releases/latest' for call in self.api.call_args_list))

    def test_resolver_semver_is_numeric_and_incomplete_is_diagnostic(self):
        self.set_version('0.1.10'); self.write_plan(bootstrap=False, old='0.1.1')
        self.manifest = release.build_manifest(self.root, self.env); self.add_ours()
        incomplete = self.add_legacy('0.1.9'); incomplete['assets'] = []
        result = release.resolve_windows_stable()
        self.assertEqual(result['latest']['version'], '0.1.10')
        self.assertIn('v0.1.9', result['diagnostics'][0])

    def test_resolver_duplicate_page_missing_manifest_invalid_manifest_rejected(self):
        legacy = self.releases['v0.1.1']
        self.responses['releases?per_page=100&page=2'] = [legacy]
        with self.assertRaisesRegex(ValueError, 'Duplicate'): release.resolve_windows_stable()
        del self.responses['releases?per_page=100&page=2']
        self.add_legacy('0.1.3')
        with self.assertRaisesRegex(ValueError, 'missing its manifest'): release.resolve_windows_stable()
        self.add_asset(self.releases['v0.1.3'], release.MANIFEST_NAME, b'{}')
        with self.assertRaises(ValueError): release.resolve_windows_stable()

    def test_newer_public_windows_version_prevents_latest_rollback(self):
        with patch.object(release, 'resolve_windows_stable', return_value={'latest': {'version': '0.2.0'}, 'diagnostics': []}):
            with self.assertRaisesRegex(ValueError, 'greater than'): self.check()
        self.assert_no_publish()

    def test_bootstrap_does_not_silently_increment_the_candidate(self):
        self.set_version('0.1.3'); self.write_plan()
        with self.assertRaisesRegex(ValueError, 'bootstrap'): self.check()
        self.assert_no_publish()

    def test_duplicate_manifest_keys_are_rejected(self):
        path = self.root / 'dist/build-manifest.json'
        path.write_text('{"schemaVersion":1,"schemaVersion":1}')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
            release.verify_build(self.root, self.approval, self.env)

    def test_symlinked_installer_is_rejected(self):
        original = self.installer.with_suffix('.real'); self.installer.rename(original)
        try:
            self.installer.symlink_to(original)
        except OSError:
            self.skipTest('Symlink creation unavailable on this runner')
        with self.assertRaisesRegex(ValueError, 'artifact'):
            release.check_artifacts(self.root, self.version)

    def test_old_public_version_must_match_approved_plan(self):
        self.write_plan(old='0.1.0')
        with self.assertRaisesRegex(ValueError, 'oldVersion'): self.check()

    def test_first_stable_requires_explicit_nonbootstrap_plan(self):
        self.releases.clear(); self.write_plan(bootstrap=False, old=None)
        self.assertEqual(self.check(), ('v0.1.2', '0.1.2'))

    def test_artifacts_windows_line_endings_missing_empty_and_wrong_version(self):
        self.checksum.write_bytes(self.checksum.read_bytes().replace(b'\n', b'\r\n'))
        self.assertEqual(release.check_artifacts(self.root, self.version), (self.installer, self.checksum))
        with self.assertRaises(ValueError): release.check_artifacts(self.root, '0.1.3')
        self.installer.write_bytes(b'')
        with self.assertRaises(ValueError): release.check_artifacts(self.root, self.version)
        self.installer.unlink()
        with self.assertRaises(ValueError): release.check_artifacts(self.root, self.version)

    def test_checksum_missing_wrong_name_format_hash_and_duplicates_rejected(self):
        valid = self.checksum.read_text()
        for content in (valid.replace('0.1.2', '0.1.1'), 'not a checksum', '0' * 64 + '  ' + self.installer.name, valid + valid):
            self.checksum.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError): release.check_artifacts(self.root, self.version)
        self.checksum.unlink()
        with self.assertRaises(ValueError): release.check_artifacts(self.root, self.version)

    def test_manifest_keeps_original_attempt_and_rejects_recovery_identity_substitution(self):
        env = {**self.env, 'GITHUB_RUN_ID': '900', 'GITHUB_RUN_ATTEMPT': '8'}
        result = release.verify_build(self.root, self.approval, env)
        self.assertEqual(result['build'], {'runId': 100, 'runAttempt': 2})
        with self.assertRaisesRegex(ValueError, 'run/attempt'):
            release.verify_build(self.root, self.approval, {**env, 'WINDOWS_BUILD_RUN_ID': '900'})

    def test_manifest_tampered_source_plan_bytes_artifact_or_unsuccessful_build_rejected(self):
        base = copy.deepcopy(self.manifest)
        for key, value in [('releaseSha', BASE), ('planSha256', '0' * 64), ('files', []), ('sources', {'../bad': '0' * 64})]:
            (self.root / 'dist/build-manifest.json').write_bytes(release.json_bytes({**base, key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError): release.verify_build(self.root, self.approval, self.env)
        (self.root / 'dist/build-manifest.json').write_bytes(release.json_bytes(base))
        artifact = self.responses['actions/artifacts/55']
        for key, value in [('expired', True), ('name', 'windows-installer-100-1'), ('digest', None), ('id', 66)]:
            old = artifact[key]; artifact[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): release.verify_build(self.root, self.approval, self.env)
            artifact[key] = old
        self.responses['actions/runs/100/attempts/2/jobs?per_page=100&page=1']['jobs'][0]['conclusion'] = 'failure'
        with self.assertRaisesRegex(ValueError, 'successfully'): release.verify_build(self.root, self.approval, self.env)
        self.assert_no_publish()

    def test_original_archive_hash_and_exact_members_are_required(self):
        original = self.archive
        self.archive = b'tampered ZIP'
        with self.assertRaisesRegex(ValueError, 'archive SHA-256'):
            release.verify_build(self.root, self.approval, self.env)
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('../installer.exe', b'unexpected')
        self.archive = content.getvalue()
        self.responses['actions/artifacts/55']['digest'] = 'sha256:' + release.sha256(self.archive)
        with self.assertRaisesRegex(ValueError, 'members'):
            release.verify_build(self.root, self.approval, self.env)
        self.archive = original
        self.assert_no_publish()

    def test_original_source_run_wrong_workflow_fork_sha_and_attempt_are_rejected(self):
        run = self.responses['actions/runs/100/attempts/2']
        for key, value in [('path', '.github/workflows/checks.yml'), ('head_repository', {'full_name': 'fork/hellgato'}),
                           ('head_sha', BASE), ('head_branch', 'feature'), ('run_attempt', 1)]:
            old = run[key]; run[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.verify_build(self.root, self.approval, self.env)
            run[key] = old
        self.assert_no_publish()

    def test_incomplete_build_job_pagination_fails_closed(self):
        self.responses['actions/runs/100/attempts/2/jobs?per_page=100&page=2']['total_count'] = 2
        with self.assertRaisesRegex(ValueError, 'pagination'):
            release.verify_build(self.root, self.approval, self.env)

    def test_historical_rebuild_preserves_exact_source_and_newer_main_run_head(self):
        self.responses['actions/runs/100/attempts/2']['head_sha'] = HEAD
        self.responses['actions/artifacts/55']['workflow_run']['head_sha'] = HEAD
        self.responses[f'compare/{SHA}...{HEAD}'] = {'status': 'ahead', 'merge_base_commit': {'sha': SHA}}
        result = release.verify_build(self.root, self.approval, self.env)
        self.assertEqual(result['releaseSha'], SHA)

    def test_unknown_existing_release_draft_prerelease_or_changed_bytes_rejected(self):
        for draft in (True, False):
            item = self.add_ours(draft=draft); item['body'] = 'unknown'
            with self.subTest(draft=draft), self.assertRaises(ValueError): release.publish_release(self.root, self.env)
        item = self.add_ours(draft=True); item['prerelease'] = True
        with self.assertRaises(ValueError): release.publish_release(self.root, self.env)
        item = self.add_ours(draft=True); self.bytes[item['assets'][0]['id']] = b'tampered'
        with self.assertRaises(ValueError): release.publish_release(self.root, self.env)
        self.assert_no_publish()

    def test_identical_completed_release_is_noop(self):
        self.add_ours()
        self.assertIn('Already confirmed', release.publish_release(self.root, self.env))
        self.assert_no_publish()

    def test_own_draft_resume_uploads_only_missing_files_never_clobbers(self):
        self.add_ours(draft=True, missing=(self.installer.name,))
        self.assertIn('Confirmed Windows stable', release.publish_release(self.root, self.env))
        uploads = [call.args[0] for call in self.run.call_args_list if call.args[0][:3] == ['gh', 'release', 'upload']]
        self.assertEqual(len(uploads), 1); self.assertEqual(uploads[0][4], str(self.installer))
        self.assertFalse(any('--clobber' in call.args[0] or '--force' in call.args[0] for call in self.run.call_args_list))

    def test_fresh_publish_creates_exact_tag_draft_validates_bytes_then_finalizes(self):
        self.assertIn('Confirmed Windows stable', release.publish_release(self.root, self.env))
        commands = [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'gh']
        self.assertIn('sha=' + SHA, commands[0]); self.assertIn('--draft', commands[1]); self.assertIn('--verify-tag', commands[1])
        self.assertEqual(Path(commands[2][4]).name, release.MANIFEST_NAME)
        self.assertEqual(commands[-1][:3], ['gh', 'release', 'edit'])
        self.assertFalse(self.releases['v0.1.2']['draft'])
        for call in self.run.call_args_list: self.assertNotIn('shell', call.kwargs)

    def test_publish_rechecks_tag_and_checksum_before_any_write(self):
        self.check(); self.tags['v0.1.2'] = {'object': {'type': 'commit', 'sha': BASE}}
        with self.assertRaisesRegex(ValueError, 'must match'): release.publish_release(self.root, self.env)
        self.tags.clear(); self.installer.write_bytes(b'tampered installer')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'): release.publish_release(self.root, self.env)
        self.assert_no_publish()

    def test_tag_change_during_upload_stops_before_draft_finalization(self):
        self.add_ours(draft=True, missing=(self.installer.name,))
        original_command = self.command
        def move_tag(args, **kwargs):
            result = original_command(args, **kwargs)
            if args[:3] == ['gh', 'release', 'upload']:
                self.tags['v0.1.2']['object']['sha'] = BASE
            return result
        self.run.side_effect = move_tag
        with self.assertRaises(ValueError): release.publish_release(self.root, self.env)
        self.assertTrue(self.releases['v0.1.2']['draft'])
        self.assertFalse(any(call.args[0][:3] == ['gh', 'release', 'edit'] for call in self.run.call_args_list))

    def test_published_missing_assets_and_unknown_extra_assets_are_not_repaired(self):
        self.add_ours(missing=(self.installer.name,))
        with self.assertRaisesRegex(ValueError, 'incomplete'): release.publish_release(self.root, self.env)
        item = self.add_ours(draft=True); self.add_asset(item, 'unknown.txt', b'unknown')
        with self.assertRaisesRegex(ValueError, 'unknown assets'): release.publish_release(self.root, self.env)
        self.assert_no_publish()


class ApiChecks(unittest.TestCase):
    def setUp(self):
        sleep_patch = patch.object(release.time, 'sleep')
        self.sleep = sleep_patch.start(); self.addCleanup(sleep_patch.stop)

    def test_only_explicitly_allowed_404_is_missing(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            for status in (401, 403, 404, 429, 500, 503):
                error = urllib.error.HTTPError('https://api.github.com', status, 'fixture', {}, None)
                with self.subTest(status=status), patch.object(release.urllib.request, 'urlopen', side_effect=error):
                    with self.assertRaises(ValueError): release.api_get('git/ref/tags/v0.1.2')
                    if status == 404: self.assertIsNone(release.api_get('releases/tags/v0.1.2', allow_missing=True))
                    else:
                        with self.assertRaises(ValueError): release.api_get('releases/tags/v0.1.2', allow_missing=True)

    def test_network_malformed_response_and_missing_token_fail_closed(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            with patch.object(release.urllib.request, 'urlopen', side_effect=urllib.error.URLError('offline')):
                with self.assertRaises(ValueError): release.api_get('releases', allow_missing=True)
            for data in (b'null', b'[]', b'not json'):
                with self.subTest(data=data), patch.object(release.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                    with self.assertRaises(ValueError): release.api_get('releases/tags/v0.1.2', allow_missing=True)
        with patch.dict(os.environ, {}, clear=True), patch.object(release.urllib.request, 'urlopen') as urlopen:
            with self.assertRaises(ValueError): release.api_get('releases')
            urlopen.assert_not_called()

    def test_transient_read_retries_are_bounded_but_authorization_never_retried(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            error = urllib.error.HTTPError('https://api.github.com', 503, 'fixture', {}, None)
            with patch.object(release.urllib.request, 'urlopen', side_effect=error) as urlopen:
                with self.assertRaises(ValueError): release.api_get('releases')
                self.assertEqual(urlopen.call_count, 3)
                self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2])
            error = urllib.error.HTTPError('https://api.github.com', 403, 'fixture', {}, None)
            with patch.object(release.urllib.request, 'urlopen', side_effect=error) as urlopen:
                with self.assertRaises(ValueError): release.api_get('releases')
                self.assertEqual(urlopen.call_count, 1)

    def test_objects_and_explicit_list_pages_are_returned(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}):
            for data in ({'tag_name': 'v0.1.1'}, [{'id': 1}]):
                with patch.object(release.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(data).encode())):
                    self.assertEqual(release.api_get('releases', array=isinstance(data, list)), data)


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); (self.root / 'package.tgz').write_bytes(b'original tarball')
        release._VERIFIED_ARCHIVES.clear()

    def verify(self, entries):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            for name, data in entries:
                archive.writestr(name, data)
        data = content.getvalue()
        return release._VERIFY_ARTIFACT.verify_artifact(self.root, 123, release.sha256(data), fetch=lambda _: data)

    def test_exact_generic_artifact_passes(self):
        self.verify([('package.tgz', b'original tarball')])

    def test_traversal_absolute_backslash_duplicate_and_tampered_members_fail(self):
        cases = [('../package.tgz', b'original tarball'), ('/package.tgz', b'original tarball'),
                 ('folder\\package.tgz', b'original tarball'), ('package.tgz', b'changed tarball')]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaises(ValueError): self.verify([entry])
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                self.verify([('package.tgz', b'original tarball')] * 2)

    def test_zip_symlink_and_local_extra_file_fail(self):
        link = zipfile.ZipInfo('package.tgz'); link.create_system = 3; link.external_attr = 0o120777 << 16
        with self.assertRaisesRegex(ValueError, 'unsafe'): self.verify([(link, b'original tarball')])
        (self.root / 'extra.txt').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'missing members'): self.verify([('package.tgz', b'original tarball')])

    def test_verification_cache_never_accepts_changed_local_bytes(self):
        self.verify([('package.tgz', b'original tarball')])
        (self.root / 'package.tgz').write_bytes(b'tampered tarball')
        with self.assertRaisesRegex(ValueError, 'differ'): self.verify([('package.tgz', b'original tarball')])


class RuntimeMetadataChecks(unittest.TestCase):
    def test_packaged_and_source_roots_use_identical_runtime_metadata(self):
        source = ast.parse((ROOT / 'app/hellgato.py').read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'read_release_metadata')
        namespace = {'json': json, 're': re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<runtime metadata>', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'app').mkdir()
            path = root / 'app/release.json'
            path.write_text('{"version":"0.1.2","channel":"stable"}')
            self.assertEqual(namespace['read_release_metadata'](root)['version'], '0.1.2')
            for data in ({'version': '0.1.1-rc.1', 'channel': 'stable'}, {'version': '0.1.2', 'channel': 'beta'}, {}):
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError): namespace['read_release_metadata'](root)
        spec = (ROOT / 'installer/hellgato.spec').read_text()
        self.assertIn("(str(root / 'app/release.json'), 'app')", spec)


class WorkflowContractChecks(unittest.TestCase):
    def test_build_is_read_only_and_preserves_security_checks(self):
        text = (ROOT / '.github/workflows/windows-build.yml').read_text()
        self.assertIn('contents: read', text); self.assertNotIn('contents: write', text)
        self.assertNotIn('GH_TOKEN', text); self.assertNotIn('windows_release.py publish', text)
        for command in ("python -m unittest discover -s test -p 'test_*.py'", '-File test/test_installer_migration.ps1',
                        './scripts/build-windows.ps1 -Iscc', './work/build-runtime/node.exe --test test/*.test.mjs',
                        'python scripts/windows_release.py artifacts', 'python scripts/windows_release.py manifest'):
            self.assertIn(command, text)
        self.assertIn('if-no-files-found: error', text); self.assertIn('dist/build-manifest.json', text)
        self.assertIn('windows-installer-${{ github.run_id }}-${{ github.run_attempt }}', text)

    def test_manual_publisher_delegates_same_coordinator(self):
        text = (ROOT / '.github/workflows/windows-release.yml').read_text()
        events = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]
        self.assertEqual(re.findall(r'^  ([a-z_]+):', events, re.MULTILINE), ['workflow_dispatch'])
        self.assertIn('default: false', events)
        self.assertNotIn('gh release create', text)
        self.assertNotIn('windows_release.py publish', text)
        self.assertIn('release.yml', text)

    def test_build_script_derives_filename_without_changing_hash_generation(self):
        text = (ROOT / 'scripts/build-windows.ps1').read_text()
        for command in ('$version = & $Python scripts/windows_release.py version', 'Get-Item "dist/Hellgato-$version-Setup.exe"',
                        'Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256', '"$checksum  $($installer.Name)"'):
            self.assertIn(command, text)
        self.assertNotIn('Hellgato-0.1.1-Setup.exe', text)


if __name__ == '__main__':
    unittest.main()

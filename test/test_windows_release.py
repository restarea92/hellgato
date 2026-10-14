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
REAL_API_GET, REAL_ASSET_BYTES, REAL_ARCHIVE_BYTES = release.api_get, release.asset_bytes, release.artifact_archive_bytes


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('installer', 'app', 'packages/windows-release', 'docs/releases', 'dist', '.release', 'scripts', 'vendor/mirabox-sdk'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ('installer/requirements-build.txt', 'scripts/prepare-windows.py', 'scripts/prepare-cora.mjs', 'vendor/mirabox-sdk/manifest.json', 'pyproject.toml', 'uv.lock'):
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
        self.write_build()
        archive_patch = patch.object(release, 'artifact_archive_bytes', side_effect=lambda identifier: self.archive)
        self.archive_read = archive_patch.start(); self.addCleanup(archive_patch.stop)
        self.run.reset_mock(); self.api.reset_mock()

    def write_build(self, *, plan_path='.release/plan.json', source_channel=None):
        self.manifest = release.build_manifest(self.root, self.env, plan_path=plan_path, source_channel=source_channel)
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            for path in (self.installer, self.checksum, self.root / 'dist/build-manifest.json'):
                archive.writestr(path.name, path.read_bytes())
        self.archive = content.getvalue()
        self.responses['actions/artifacts/55']['digest'] = 'sha256:' + release.sha256(self.archive)
        release._VERIFIED_ARCHIVES.clear()

    def set_version(self, version):
        self.version = version
        (self.root / 'packages/windows-release/package.json').write_text(json.dumps(
            {'name': '@hellgato/windows-release', 'private': True, 'version': version}))
        (self.root / 'installer/hellgato.iss').write_text('#include "version.iss"\n[Setup]\nAppVersion={#AppVersion}\n')
        (self.root / 'installer/version.iss').write_text(f'#define AppVersion "{version}"\n#define AppVersionNumeric "{release.numeric_version(version)}"\n')
        (self.root / 'app/release.json').write_text(json.dumps({'version': version, 'channel': release.version_channel(version)}))
        (self.root / f'docs/releases/{version}.md').write_text(f'# Hellgato {version}\n\nChanges.\n')
        self.installer = self.root / f'dist/Hellgato-{version}-Setup.exe'
        self.installer.write_bytes(b'unit test installer, not executable')
        self.checksum = self.installer.with_name(self.installer.name + '.sha256')
        self.checksum.write_text(f'{release.sha256(self.installer.read_bytes())}  {self.installer.name}\n')

    def write_plan(self, *, bootstrap=True, old='0.1.1', public_baseline=None):
        self.plan = {'schemaVersion': 1, 'repository': release.REPOSITORY, 'baseSha': BASE, 'bootstrap': bootstrap,
                     'components': [{'component': 'windows', 'oldVersion': old, 'version': self.version,
                                     'channel': 'stable', 'tag': 'v' + self.version, 'notes': f'docs/releases/{self.version}.md'}]}
        if public_baseline is not None:
            self.plan['components'][0]['publicBaseline'] = public_baseline
        path = self.root / '.release/plan.json'; path.write_bytes(release.json_bytes(self.plan))
        self.approval = {'schemaVersion': 1, 'repository': release.REPOSITORY, 'releaseSha': SHA,
                         'planSha256': release.sha256(path.read_bytes()), 'baseSha': BASE,
                         'bootstrap': bootstrap, 'components': self.plan['components'],
                         'pullRequest': {'number': 7, 'headSha': HEAD, 'mergedBy': 'restarea92'}}
        (self.root / '.release/approval.json').write_bytes(release.json_bytes(self.approval))
        if not bootstrap:
            self.responses['pulls/7']['head']['ref'] = 'changeset-release/main'
            self.responses['pulls/7']['user']['login'] = 'github-actions[bot]'

    def public_baseline(self, *, version='0.1.1', source='0.1.2'):
        item = self.releases['v' + version]
        assets = {asset['name']: asset for asset in item['assets']}
        self.tags[item['tag_name']] = {'object': {'type': 'commit', 'sha': BASE}}
        return {'schemaVersion': 1, 'component': 'windows', 'channel': release.version_channel(version), 'sourceVersion': source,
                'version': version, 'tag': item['tag_name'], 'releaseId': item['id'], 'releaseSha': BASE,
                'installerSha256': release.sha256(self.bytes[assets[f'Hellgato-{version}-Setup.exe']['id']]),
                'manifestSha256': release.sha256(self.bytes[assets[release.MANIFEST_NAME]['id']]) if release.MANIFEST_NAME in assets else None,
                'reason': 'Reviewed prepared source and the exact previous public Windows artifacts.'}

    def prepare_public_baseline(self, *, version='0.1.3', source='0.1.2', public='0.1.1'):
        baseline = self.public_baseline(version=public, source=source)
        for path in (self.root / 'dist').iterdir():
            path.unlink()
        self.set_version(version)
        self.write_plan(bootstrap=False, old=source, public_baseline=baseline)
        self.write_build()
        return baseline

    def add_previous_manifest(self, version='0.1.1'):
        item = self.releases['v' + version]
        manifest = copy.deepcopy(self.public_manifest())
        manifest.update(version=version, tag=item['tag_name'], releaseSha=BASE, channel=release.version_channel(version),
                        files=[{'name': asset['name'], 'size': asset['size'], 'sha256': release.sha256(self.bytes[asset['id']])}
                               for asset in item['assets']])
        self.tags[item['tag_name']] = {'object': {'type': 'commit', 'sha': BASE}}
        return self.add_asset(item, release.MANIFEST_NAME, release.json_bytes(manifest))

    def refresh_channel_plan(self, *, candidate=False):
        filename = '.release/candidate-plan.json' if candidate else '.release/plan.json'
        path = self.root / filename
        path.write_bytes(release.json_bytes(self.plan))
        self.approval.update(planSha256=release.sha256(path.read_bytes()), bootstrap=False,
                             components=self.plan['components'])
        (self.root / '.release/approval.json').write_bytes(release.json_bytes(self.approval))
        return filename

    def prepare_channel_release(self, *, version='0.1.3-beta.0', source='0.1.2', public='0.1.1', candidate=False):
        baseline = self.public_baseline(version=public, source=source)
        for path in (self.root / 'dist').iterdir():
            path.unlink()
        self.set_version(version)
        self.write_plan(bootstrap=False, old=source, public_baseline=None if candidate else baseline)
        channel = release.version_channel(version)
        policy = release.channel_policy(channel)
        self.plan['components'][0]['channel'] = channel
        before = {'mode': 'pre', 'tag': 'beta'} if channel == 'stable' or release.version_channel(source) == 'beta' else None
        self.plan = {'schemaVersion': 2 if candidate else 3, 'repository': release.REPOSITORY,
                     'candidateOnly': candidate, 'branch': 'next' if candidate else policy['branch'],
                     'baseSha': BASE, 'channel': channel,
                     'transition': 'exit' if channel == 'stable' else 'advance' if before else 'enter',
                     'policy': policy, 'changesets': [], 'archivedBefore': [], 'archivedAfter': [],
                     'preBefore': before, 'preAfter': {'mode': 'pre', 'tag': 'beta'} if channel == 'beta' else None,
                     'components': self.plan['components'], 'generatedFiles': []}
        (self.root / '.changeset').mkdir(exist_ok=True)
        pre_path = self.root / '.changeset/pre.json'
        if channel == 'beta':
            pre_path.write_bytes(release.json_bytes(self.plan['preAfter']))
        elif pre_path.exists():
            pre_path.unlink()
        paths = ['packages/windows-release/package.json', 'app/release.json', 'installer/version.iss',
                 f'docs/releases/{version}.md']
        if channel == 'beta':
            paths.append('.changeset/pre.json')
        self.plan['generatedFiles'] = [{'path': name, 'sha256': release.sha256((self.root / name).read_bytes())}
                                      for name in sorted(paths)]
        filename = self.refresh_channel_plan(candidate=candidate)
        branch = policy['branch']
        self.env['GITHUB_REF'] = 'refs/heads/' + branch
        self.responses['pulls/7']['base']['ref'] = branch
        self.responses['pulls/7']['head']['ref'] = policy['releaseBranch']
        self.responses[f'compare/{SHA}...{branch}'] = {'status': 'identical', 'merge_base_commit': {'sha': SHA}}
        self.responses['actions/artifacts/55']['workflow_run']['head_branch'] = branch
        self.responses['actions/runs/100/attempts/2']['head_branch'] = branch
        self.write_build(plan_path=filename)
        return baseline

    def add_previous_public(self, version):
        item = self.add_legacy(version)
        item['prerelease'] = release.version_channel(version) == 'beta'
        self.add_previous_manifest(version)
        return item

    def beta_simulation(self):
        return release.BetaReleaseSimulation(checkout=SHA, responses=self.responses, releases=self.releases,
                                             tags=self.tags, assets=self.bytes, archives={55: self.archive})

    def simulate_beta(self, state):
        with patch.object(release, 'api_get', REAL_API_GET), patch.object(release, 'asset_bytes', REAL_ASSET_BYTES), \
                patch.object(release, 'artifact_archive_bytes', REAL_ARCHIVE_BYTES), \
                patch.object(release.subprocess, 'run', side_effect=AssertionError('Simulation reached subprocess')), \
                patch.object(release.urllib.request, 'urlopen', side_effect=AssertionError('Simulation reached network')):
            return release.simulate_beta_publication(self.root, state, self.env)

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
        item = {'id': self.next_id, 'tag_name': 'v' + self.version, 'draft': draft, 'prerelease': release.version_channel(self.version) == 'beta',
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

    def test_ordinary_stable_build_does_not_bind_a_lingering_release_plan(self):
        manifest = release.build_manifest(self.root, self.env)
        self.assertIsNone(manifest['planSha256'])
        with self.assertRaisesRegex(ValueError, 'approved source/plan/version'):
            release.verify_build(self.root, self.approval, self.env)
        (self.root / '.release/plan.json').write_text('malformed obsolete plan')
        self.assertIsNone(release.build_manifest(self.root, self.env)['planSha256'])
        with self.assertRaises(ValueError):
            release.build_manifest(self.root, self.env, plan_path='.release/plan.json')

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
        self.manifest = release.build_manifest(self.root, self.env, plan_path='.release/plan.json'); self.add_ours()
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

    def test_unpublished_prepared_windows_baseline_blocks_normal_release(self):
        self.set_version('0.1.3')
        self.write_plan(bootstrap=False, old='0.1.2')
        # Changesets uses source 0.1.2, but the fixture's latest public release
        # remains 0.1.1. Both enabled and read-only preflight must stop.
        with self.assertRaisesRegex(ValueError, 'oldVersion does not match'):
            self.check()
        with self.assertRaisesRegex(ValueError, 'oldVersion does not match'):
            release.check_release(self.root, self.env, require_enabled=False)
        self.assert_no_publish()

    def test_reviewed_public_baseline_allows_prepared_source_without_rewriting_old_version(self):
        baseline = self.prepare_public_baseline()
        self.assertEqual(self.plan['components'][0]['oldVersion'], '0.1.2')
        self.assertEqual(baseline['version'], '0.1.1')
        self.assertEqual(self.check(), ('v0.1.3', '0.1.3'))
        self.assertEqual(release.check_release(self.root, self.env, require_enabled=False), ('v0.1.3', '0.1.3'))
        self.api.assert_any_call('releases/tags/v0.1.1')
        self.assert_no_publish(); self.assertNotIn('v0.1.3', self.tags)
        latest = release.resolve_windows_stable()['latest']
        self.assertEqual(set(latest), {'version', 'tag', 'url', 'installerUrl', 'checksumUrl'})

    def test_public_baseline_requires_exact_shape_identity_reason_and_canonical_versions(self):
        baseline = self.prepare_public_baseline()
        component = self.plan['components'][0]
        invalid = [None, [], {}, {**baseline, 'unexpected': True}]
        invalid += [{key: value for key, value in baseline.items() if key != missing} for missing in baseline]
        invalid += [{**baseline, key: value} for key, value in (
            ('schemaVersion', True), ('schemaVersion', 1.0), ('schemaVersion', 2),
            ('component', 'npm'), ('channel', 'beta'), ('sourceVersion', '0.1.1'),
            ('sourceVersion', '0.1.2-beta.1'), ('version', '00.1.1'), ('version', '0.1.1-beta.1'),
            ('tag', 'v0.1.0'), ('releaseId', True), ('releaseId', '11'), ('releaseId', 1.5),
            ('releaseId', 0), ('releaseId', -1), ('releaseId', 2 ** 53), ('releaseSha', 'A' * 40), ('releaseSha', BASE[:7]),
            ('installerSha256', 'A' * 64), ('installerSha256', None), ('manifestSha256', 'A' * 64),
            ('reason', None), ('reason', ' ' * 40), ('reason', ' ' * 10 + 'x' * 29 + ' ' * 10))]
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                release.validate_public_baseline({**component, 'publicBaseline': record}, bootstrap=False)
        # An explicitly malformed record must stop at approval, before any remote read.
        self.api.reset_mock()
        self.write_plan(bootstrap=False, old='0.1.2', public_baseline={**baseline, 'unexpected': True})
        with self.assertRaisesRegex(ValueError, 'public baseline fields'):
            self.check()
        self.api.assert_not_called(); self.assert_no_publish()

    def test_public_baseline_requires_source_tie_and_strict_candidate_order(self):
        baseline = self.prepare_public_baseline()
        component = self.plan['components'][0]
        for version, source, candidate in [('0.1.3', '0.1.2', '0.1.4'), ('0.1.1', '0.1.2', '0.1.2'),
                                           ('0.1.1', '0.1.3', '0.1.2'), ('0.1.10', '0.1.9', '0.1.11')]:
            record = {**baseline, 'version': version, 'tag': 'v' + version,
                      'sourceVersion': source, 'manifestSha256': 'a' * 64}
            with self.subTest(version=version, source=source, candidate=candidate), self.assertRaisesRegex(ValueError, 'above source'):
                release.validate_public_baseline({**component, 'oldVersion': source, 'version': candidate,
                                                  'publicBaseline': record}, bootstrap=False)
        with self.assertRaisesRegex(ValueError, 'Invalid reviewed'):
            release.validate_public_baseline({**component, 'oldVersion': '0.1.0'}, bootstrap=False)
        record = {**baseline, 'version': '0.1.9', 'tag': 'v0.1.9',
                  'sourceVersion': '0.1.10', 'manifestSha256': 'a' * 64}
        self.assertEqual(release.validate_public_baseline({**component, 'oldVersion': '0.1.10', 'version': '0.1.11',
                                                           'publicBaseline': record}, bootstrap=False), record)

    def test_public_baseline_cannot_relax_bootstrap_or_nonlegacy_manifest_requirement(self):
        baseline = self.public_baseline(source='0.1.1')
        self.write_plan(public_baseline=baseline)
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            self.check()
        self.api.assert_not_called()
        baseline = self.prepare_public_baseline()
        record = {**baseline, 'version': '0.1.2', 'tag': 'v0.1.2', 'manifestSha256': None}
        with self.assertRaisesRegex(ValueError, 'Invalid reviewed'):
            release.validate_public_baseline({**self.plan['components'][0], 'publicBaseline': record}, bootstrap=False)
        self.assert_no_publish()

    def test_public_baseline_rejects_changed_or_missing_latest_stable(self):
        self.prepare_public_baseline()
        self.add_legacy('0.1.2'); self.add_previous_manifest('0.1.2')
        with self.assertRaisesRegex(ValueError, 'baseline does not match the latest'):
            self.check()
        self.releases.clear()
        with self.assertRaisesRegex(ValueError, 'baseline does not match the latest'):
            self.check()
        self.assert_no_publish()

    def test_public_baseline_rejects_recreated_release_or_changed_dereferenced_source(self):
        baseline = self.prepare_public_baseline()
        item = self.releases['v0.1.1']; item['id'] += 1000
        with self.assertRaisesRegex(ValueError, 'release ID, tag, or source'):
            self.check()
        item['id'] = baseline['releaseId']
        self.tags['v0.1.1'] = {'object': {'type': 'tag', 'sha': HEAD}}
        self.responses[f'git/tags/{HEAD}'] = {'object': {'type': 'commit', 'sha': BASE}}
        self.assertEqual(self.check(), ('v0.1.3', '0.1.3'))
        self.responses[f'git/tags/{HEAD}']['object']['sha'] = SHA
        with self.assertRaisesRegex(ValueError, 'release ID, tag, or source'):
            self.check()
        self.assert_no_publish()

    def test_public_baseline_rejects_consistently_replaced_installer_and_checksum(self):
        self.prepare_public_baseline()
        assets = {asset['name']: asset for asset in self.releases['v0.1.1']['assets']}
        name, content = 'Hellgato-0.1.1-Setup.exe', b'replacement installer with its own valid checksum'
        for filename, data in [(name, content), (name + '.sha256', f'{release.sha256(content)}  {name}\n'.encode())]:
            self.bytes[assets[filename]['id']] = data
            assets[filename]['size'] = len(data)
        with self.assertRaisesRegex(ValueError, 'baseline installer or checksum'):
            self.check()
        self.assert_no_publish()

    def test_public_baseline_binds_raw_manifest_bytes_and_detects_added_or_removed_manifest(self):
        asset = self.add_previous_manifest()
        self.prepare_public_baseline()
        self.assertEqual(self.check(), ('v0.1.3', '0.1.3'))
        # The same valid JSON, with different whitespace, still breaks exact-byte approval.
        self.bytes[asset['id']] += b'\n'
        asset['size'] += 1
        with self.assertRaisesRegex(ValueError, 'manifest bytes have changed'):
            self.check()
        self.releases['v0.1.1']['assets'].remove(asset)
        with self.assertRaisesRegex(ValueError, 'manifest bytes have changed'):
            self.check()
        self.prepare_public_baseline()
        self.add_previous_manifest()
        with self.assertRaisesRegex(ValueError, 'manifest bytes have changed'):
            self.check()
        self.assert_no_publish()

    def test_public_baseline_accepts_verified_nonlegacy_manifest(self):
        self.add_legacy('0.1.2'); self.add_previous_manifest('0.1.2')
        self.prepare_public_baseline(version='0.1.4', source='0.1.3', public='0.1.2')
        self.assertEqual(self.check(), ('v0.1.4', '0.1.4'))
        self.assert_no_publish()

    def test_public_baseline_preserves_owner_merge_and_exact_source_provenance(self):
        self.prepare_public_baseline()
        self.responses['pulls/7']['merged_by']['login'] = 'someone-else'
        with self.assertRaisesRegex(ValueError, 'owner-merged'):
            self.check()
        self.responses['pulls/7']['merged_by']['login'] = 'restarea92'
        self.responses[f'git/commits/{HEAD}']['tree']['sha'] = BASE
        with self.assertRaisesRegex(ValueError, 'approved tree mismatch'):
            self.check()
        self.responses[f'git/commits/{HEAD}']['tree']['sha'] = TREE
        self.add_ours(draft=True)
        path = self.root / 'vendor/mirabox-sdk/manifest.json'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'source/toolchain provenance mismatch'):
            self.check()
        self.assert_no_publish()

    def test_reviewed_baseline_publication_and_completed_retry_remain_immutable(self):
        self.prepare_public_baseline()
        self.assertIn('Confirmed Windows stable', release.publish_release(self.root, self.env))
        self.assertFalse(self.releases['v0.1.3']['draft'])
        self.run.reset_mock()
        self.assertIn('Already confirmed', release.publish_release(self.root, self.env))
        with patch.object(release, 'resolve_windows_stable', return_value={'latest': {'version': '0.2.0'}, 'diagnostics': []}):
            self.assertIn('Already confirmed', release.publish_release(self.root, self.env))
        self.assert_no_publish()

    def test_completed_baseline_retry_still_rejects_malformed_record_and_changed_bytes(self):
        baseline = self.prepare_public_baseline(); item = self.add_ours()
        self.write_plan(bootstrap=False, old='0.1.2', public_baseline={**baseline, 'reason': 'too short'})
        with self.assertRaisesRegex(ValueError, 'Invalid reviewed'):
            release.publish_release(self.root, self.env)
        self.write_plan(bootstrap=False, old='0.1.2', public_baseline=baseline)
        self.bytes[item['assets'][0]['id']] = b'changed installer'
        with self.assertRaises(ValueError):
            release.publish_release(self.root, self.env)
        self.assert_no_publish()

    def test_public_baseline_changed_during_upload_stops_before_finalization(self):
        self.prepare_public_baseline(); self.add_ours(draft=True, missing=(self.installer.name,))
        original_command = self.command
        def change_public_identity(args, **kwargs):
            result = original_command(args, **kwargs)
            if args[:3] == ['gh', 'release', 'upload']:
                self.releases['v0.1.1']['id'] += 1000
            return result
        self.run.side_effect = change_public_identity
        with self.assertRaisesRegex(ValueError, 'release ID, tag, or source'):
            release.publish_release(self.root, self.env)
        self.assertTrue(self.releases['v0.1.3']['draft'])
        self.assertFalse(any(call.args[0][:3] == ['gh', 'release', 'edit'] for call in self.run.call_args_list))

    def test_windows_preflight_does_not_require_selected_npm_publication(self):
        self.set_version('0.1.3')
        self.write_plan(bootstrap=False, old='0.1.2')
        npm = {'component': 'npm', 'oldVersion': '0.1.0', 'version': '0.2.0',
               'channel': 'stable', 'tag': 'hellgato@0.2.0', 'notes': 'docs/releases/npm-0.2.0.md',
               'packages': [{'name': name, 'oldVersion': '0.1.0', 'version': '0.2.0', 'tag': name + '@0.2.0'}
                            for name in ('@hellgato/core', 'hellgato')]}
        self.plan['components'].append(npm)
        plan_path = self.root / '.release/plan.json'
        plan_path.write_bytes(release.json_bytes(self.plan))
        self.approval['components'] = self.plan['components']
        self.approval['planSha256'] = release.sha256(plan_path.read_bytes())
        (self.root / '.release/approval.json').write_bytes(release.json_bytes(self.approval))
        with patch.object(release, 'resolve_windows_stable', return_value={'latest': {'version': '0.1.2'}, 'diagnostics': []}):
            self.assertEqual(self.check(), ('v0.1.3', '0.1.3'))
        self.assertFalse(any('npm' in call.args[0] for call in self.api.call_args_list))
        self.assert_no_publish()

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

    def test_channel_numeric_versions_preserve_beta_sequence_and_stable_order(self):
        for version, expected in [('0.1.3-beta.0', '0.1.3.1'), ('0.1.3-beta.10', '0.1.3.11'),
                                  ('0.1.3-beta.65533', '0.1.3.65534'), ('0.1.3', '0.1.3.65535')]:
            with self.subTest(version=version):
                self.assertEqual(release.numeric_version(version), expected)
        for version in ('0.1.3-beta.65534', '65536.0.0', '1.65536.0-beta.0'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.numeric_version(version)
        for version in ('0.1.3-beta', '0.1.3-beta.01', '0.1.3-rc.1', '0.1.3+build', '01.1.3-beta.1'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.version_channel(version)
        self.assertLess(release.version_key('0.1.3-beta.9'), release.version_key('0.1.3-beta.10'))
        self.assertLess(release.version_key('0.1.3-beta.65533'), release.version_key('0.1.3'))

    def test_beta_build_requires_exact_plan_and_binds_numeric_runtime_and_generated_bytes(self):
        self.prepare_channel_release()
        with self.assertRaisesRegex(ValueError, 'explicit exact'):
            release.installer_version(self.root)
        self.assertEqual(release.installer_version(self.root, plan_path='.release/plan.json'), '0.1.3-beta.0')
        self.assertEqual(self.manifest['channel'], 'beta')
        self.assertEqual(self.manifest['planSha256'], self.approval['planSha256'])
        self.assertIn('pyproject.toml', self.manifest['sources']); self.assertIn('uv.lock', self.manifest['sources'])
        path = self.root / 'installer/version.iss'; original = path.read_bytes()
        path.write_bytes(original.replace(b'0.1.3.1', b'0.1.3.0'))
        with self.assertRaisesRegex(ValueError, 'AppVersion/include'):
            release.installer_version(self.root, plan_path='.release/plan.json')
        path.write_bytes(original + b'\n')
        with self.assertRaisesRegex(ValueError, 'generated-file bytes'):
            release.installer_version(self.root, plan_path='.release/plan.json')
        self.assert_no_publish()

    def test_artifact_only_beta_plan_cannot_be_used_as_public_release_approval(self):
        self.prepare_channel_release(candidate=True)
        self.assertEqual(release.installer_version(self.root, plan_path='.release/candidate-plan.json'), '0.1.3-beta.0')
        with self.assertRaises(ValueError): self.check()
        (self.root / '.release/plan.json').write_bytes((self.root / '.release/candidate-plan.json').read_bytes())
        with self.assertRaisesRegex(ValueError, 'channel plan'):
            self.check()
        self.assert_no_publish()

    def test_source_beta_ci_ignores_stale_plan_and_cannot_supply_public_artifacts(self):
        self.prepare_channel_release()
        plan = self.root / '.release/plan.json'; original = plan.read_bytes()
        plan.write_text('not a valid, current release plan')
        self.assertEqual(release.installer_version(self.root, source_channel='beta'), '0.1.3-beta.0')
        manifest = release.build_manifest(self.root, self.env, source_channel='beta')
        self.assertIsNone(manifest['planSha256'])
        self.assertEqual(manifest['channel'], 'beta')
        plan.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'approved source/plan/version'):
            release.verify_build(self.root, self.approval, self.env)
        self.set_version('0.1.2')
        self.assertEqual(release.installer_version(self.root, source_channel='beta'), '0.1.2')
        self.assertIsNone(release.build_manifest(self.root, self.env, source_channel='beta')['planSha256'])
        self.assert_no_publish()

    def test_source_beta_ci_requires_canonical_active_state_and_exclusive_binding(self):
        self.prepare_channel_release()
        for state in ({}, {'mode': 'exit', 'tag': 'beta'}, {'mode': 'pre', 'tag': 'rc'},
                      {'mode': 'pre', 'tag': 'beta', 'unexpected': True}):
            (self.root / '.changeset/pre.json').write_bytes(release.json_bytes(state))
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'prerelease state'):
                release.installer_version(self.root, source_channel='beta')
        with self.assertRaisesRegex(ValueError, 'exclusive'):
            release.installer_version(self.root, plan_path='.release/plan.json', source_channel='beta')
        (self.root / '.changeset/pre.json').unlink()
        with self.assertRaises(ValueError): release.installer_version(self.root, source_channel='beta')
        self.assert_no_publish()

    def test_channel_plan_rejects_malformed_policy_identity_source_and_missing_baseline(self):
        self.prepare_channel_release()
        original = copy.deepcopy(self.plan)
        mutations = [('schemaVersion', 2), ('schemaVersion', True), ('candidateOnly', True), ('branch', 'main'),
                     ('channel', 'stable'), ('policy', release.channel_policy('stable')), ('transition', 'exit'),
                     ('baseSha', BASE[:7]), ('unexpected', True), ('generatedFiles', []), ('preAfter', None)]
        for key, value in mutations:
            self.plan = {**copy.deepcopy(original), key: value}; self.refresh_channel_plan()
            with self.subTest(key=key), self.assertRaises(ValueError): self.check()
        for mutate in ('missing', 'source', 'channel', 'manifest'):
            self.plan = copy.deepcopy(original)
            baseline = self.plan['components'][0]['publicBaseline']
            if mutate == 'missing': del self.plan['components'][0]['publicBaseline']
            elif mutate == 'source': baseline['sourceVersion'] = '0.1.1'
            elif mutate == 'channel': baseline['channel'] = 'beta'
            else: baseline.update(version='0.1.2-beta.0', tag='v0.1.2-beta.0', channel='beta', manifestSha256=None)
            self.refresh_channel_plan()
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.check()
        self.api.assert_not_called(); self.assert_no_publish()

    def test_beta_approval_and_original_artifact_require_exact_next_provenance(self):
        self.prepare_channel_release()
        self.assertEqual(self.check(), ('v0.1.3-beta.0', '0.1.3-beta.0'))
        self.api.assert_any_call(f'compare/{SHA}...next')
        self.assertEqual(release.verify_build(self.root, self.approval, self.env)['channel'], 'beta')
        remote = self.responses['pulls/7']
        mutations = [(remote['base'], 'ref', 'main'), (remote['head'], 'ref', 'changeset-release/main'),
                     (remote['merged_by'], 'login', 'someone-else'), (remote['head']['repo'], 'full_name', 'fork/hellgato')]
        for item, key, value in mutations:
            old = item[key]; item[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.check()
            item[key] = old
        for item in (self.responses['actions/artifacts/55']['workflow_run'], self.responses['actions/runs/100/attempts/2']):
            item['head_branch'] = 'main'
            with self.assertRaises(ValueError): release.verify_build(self.root, self.approval, self.env)
            item['head_branch'] = 'next'
        self.assert_no_publish()

    def test_beta_resolver_is_channel_isolated_numeric_and_manifest_verified(self):
        self.add_previous_public('0.1.3-beta.9'); self.add_previous_public('0.1.3-beta.10')
        self.add_previous_public('9.0.0')
        self.assertEqual(release.resolve_windows_beta()['latest']['version'], '0.1.3-beta.10')
        self.assertEqual(release.resolve_windows_stable()['latest']['version'], '9.0.0')
        self.assertFalse(any(call.args[0] == 'releases/latest' for call in self.api.call_args_list))
        item = self.releases['v0.1.3-beta.10']
        item['assets'] = [asset for asset in item['assets'] if asset['name'] != release.MANIFEST_NAME]
        with self.assertRaisesRegex(ValueError, 'missing its manifest'): release.resolve_windows_beta()
        self.assertEqual(release.resolve_windows_stable()['latest']['version'], '9.0.0')
        self.assert_no_publish()

    def test_first_beta_baseline_requires_absent_public_beta_and_exact_public_stable(self):
        self.prepare_channel_release()
        self.assertEqual(self.check(), ('v0.1.3-beta.0', '0.1.3-beta.0'))
        self.add_previous_public('0.1.2-beta.0')
        with self.assertRaisesRegex(ValueError, 'first beta baseline'):
            self.check()
        del self.releases['v0.1.2-beta.0']
        self.releases['v0.1.1']['id'] += 1000
        with self.assertRaisesRegex(ValueError, 'release ID, tag, or source'):
            self.check()
        self.assert_no_publish()

    def test_subsequent_beta_binds_latest_beta_instead_of_global_latest(self):
        self.add_previous_public('0.1.3-beta.0')
        self.prepare_channel_release(version='0.1.3-beta.1', source='0.1.3-beta.0', public='0.1.3-beta.0')
        self.assertEqual(self.check(), ('v0.1.3-beta.1', '0.1.3-beta.1'))
        self.releases['v0.1.3-beta.0']['id'] += 1000
        with self.assertRaisesRegex(ValueError, 'release ID, tag, or source'):
            self.check()
        self.assert_no_publish()

    def test_stable_promotion_binds_beta_baseline_and_cannot_regress_public_stable(self):
        self.add_previous_public('0.1.3-beta.1')
        self.prepare_channel_release(version='0.1.3', source='0.1.3-beta.1', public='0.1.3-beta.1')
        self.assertEqual(self.check(), ('v0.1.3', '0.1.3'))
        self.add_previous_public('0.1.4')
        with self.assertRaisesRegex(ValueError, 'greater than'):
            self.check()
        self.assert_no_publish()

    def test_beta_same_engine_simulation_never_uses_live_io_or_mutates_latest(self):
        self.prepare_channel_release(); state = self.beta_simulation()
        stable = copy.deepcopy(state.releases['v0.1.1'])
        self.assertIn('Confirmed Windows beta', self.simulate_beta(state))
        self.assertEqual(state.releases['v0.1.1'], stable)
        self.assertFalse(state.releases['v0.1.3-beta.0']['draft'])
        self.assertTrue(state.releases['v0.1.3-beta.0']['prerelease'])
        commands = state.commands
        create = next(args for args in commands if args[:3] == ['gh', 'release', 'create'])
        self.assertIn('--prerelease', create)
        self.assertEqual(commands[-1][-1], '--prerelease=true')
        self.assertFalse(any(value.startswith('--latest') for args in commands for value in args))
        count = len(commands)
        self.assertIn('Already confirmed', self.simulate_beta(state))
        self.assertEqual(len(state.commands), count)
        self.next_id = state.next_id + 100
        newer = self.add_previous_public('0.1.4-beta.0')
        state.releases[newer['tag_name']] = copy.deepcopy(newer)
        state.tags[newer['tag_name']] = copy.deepcopy(self.tags[newer['tag_name']])
        state.assets.update(self.bytes)
        self.assertIn('Already confirmed', self.simulate_beta(state))
        self.assertEqual(len(state.commands), count)
        self.assertIsNone(release._SIMULATION.get())

    def test_beta_public_engine_remains_approval_gated_and_does_not_require_npm(self):
        self.prepare_channel_release()
        self.api.reset_mock()
        with self.assertRaisesRegex(ValueError, 'enabled shared'):
            release.publish_release(self.root, {**self.env, 'RELEASE_ENABLED': 'false'})
        self.api.assert_not_called(); self.assert_no_publish()
        self.assertIn('Confirmed Windows beta', release.publish_release(self.root, self.env))
        commands = [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'gh']
        self.assertTrue(self.releases['v0.1.3-beta.0']['prerelease'])
        self.assertFalse(any(value.startswith('--latest') for args in commands for value in args))
        self.assertFalse(any('npm' in call.args[0] for call in self.api.call_args_list))

    def test_beta_simulation_rejects_unknown_drafts_stale_public_identity_and_tampered_build(self):
        self.prepare_channel_release()
        state = self.beta_simulation(); state.releases['v0.1.1']['id'] += 1000
        with self.assertRaises(ValueError): self.simulate_beta(state)
        self.assertEqual(state.commands, [])
        item = self.add_ours(draft=True); item['body'] = 'unknown draft'
        state = self.beta_simulation()
        with self.assertRaisesRegex(ValueError, 'unknown source'): self.simulate_beta(state)
        self.assertEqual(state.commands, [])
        del self.releases['v0.1.3-beta.0']; del self.tags['v0.1.3-beta.0']
        state = self.beta_simulation(); state.archives[55] = b'replaced artifact'
        release._VERIFIED_ARCHIVES.clear()
        with self.assertRaisesRegex(ValueError, 'archive SHA-256'): self.simulate_beta(state)
        self.assertEqual(state.commands, [])
        self.assertIsNone(release._SIMULATION.get())

    def test_beta_simulation_detects_baseline_change_during_upload_before_finalization(self):
        self.prepare_channel_release(); self.add_ours(draft=True, missing=(self.installer.name,))
        state = self.beta_simulation()
        command = release.BetaReleaseSimulation.command
        def mutate_baseline(simulation, args, *, cwd):
            command(simulation, args, cwd=cwd)
            if args[:3] == ['gh', 'release', 'upload']:
                simulation.releases['v0.1.1']['id'] += 1000
        with patch.object(release.BetaReleaseSimulation, 'command', mutate_baseline):
            with self.assertRaisesRegex(ValueError, 'release ID, tag, or source'):
                self.simulate_beta(state)
        self.assertTrue(state.releases['v0.1.3-beta.0']['draft'])
        self.assertFalse(any(args[:3] == ['gh', 'release', 'edit'] for args in state.commands))

    def test_beta_cli_build_binding_and_source_ci_contract(self):
        self.prepare_channel_release()
        for mode in (['--plan', '.release/plan.json'], ['--source-channel', 'beta']):
            for command in ('version', 'artifacts', 'manifest'):
                with self.subTest(mode=mode, command=command), patch.object(release, '__file__', str(self.root / 'scripts/windows_release.py')), \
                        patch.object(release.sys, 'argv', ['windows_release.py', command, *mode]), \
                        patch.dict(os.environ, self.env), patch('sys.stdout', new_callable=io.StringIO) as output:
                    release.main()
                    self.assertTrue(output.getvalue().strip())
        self.assert_no_publish()

    def test_stable_promotion_publication_uses_main_provenance_and_stable_latest(self):
        self.add_previous_public('0.1.3-beta.1')
        self.prepare_channel_release(version='0.1.3', source='0.1.3-beta.1', public='0.1.3-beta.1')
        self.assertIn('Confirmed Windows stable', release.publish_release(self.root, self.env))
        self.api.assert_any_call(f'compare/{SHA}...main')
        self.assertFalse(self.releases['v0.1.3']['prerelease'])
        self.assertTrue(self.releases['v0.1.3-beta.1']['prerelease'])
        commands = [call.args[0] for call in self.run.call_args_list if call.args[0][0] == 'gh']
        create = next(args for args in commands if args[:3] == ['gh', 'release', 'create'])
        self.assertNotIn('--prerelease', create)
        self.assertEqual(commands[-1][-1], '--latest')
        self.assertFalse(any('npm' in call.args[0] for call in self.api.call_args_list))
        self.run.reset_mock()
        self.assertIn('Already confirmed', release.publish_release(self.root, self.env))
        self.assert_no_publish()

    def test_beta_completed_retry_never_repairs_changed_public_asset_bytes(self):
        self.prepare_channel_release(); state = self.beta_simulation()
        self.simulate_beta(state)
        count = len(state.commands)
        item = state.releases['v0.1.3-beta.0']
        installer = next(asset for asset in item['assets'] if asset['name'] == self.installer.name)
        changed = b'changed public installer bytes'
        state.assets[installer['id']] = changed; installer['size'] = len(changed)
        with self.assertRaises(ValueError): self.simulate_beta(state)
        self.assertEqual(len(state.commands), count)
        self.assertFalse(item['draft'])
        self.assertIsNone(release._SIMULATION.get())


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
        source = ast.parse((ROOT / 'apps/stream-dog/python/hellgato_stream_dog/launcher.py').read_text())
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
        for command in ("./.venv/Scripts/python.exe -m unittest discover -s test -p 'test_*.py'", '-File test/test_installer_migration.ps1',
                        "./scripts/build-windows.ps1 -Python (Join-Path $pwd '.venv/Scripts/python.exe') -Iscc", './work/build-runtime/node.exe --test test/*.test.mjs',
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

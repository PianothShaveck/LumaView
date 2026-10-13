"""Exercise publication against local Git repositories, never GitHub."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env=env, check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def source_tree(path):
    path.mkdir()
    contents = {
        'lumaview/__init__.py': "__version__ = '0.1.2'\n",
        'lumaview/bilevel.py': '# converter\n',
        'Install.command': '#!/bin/bash\nexit 0\n',
        'scripts/bootstrap_macos.sh': '#!/bin/bash\nexit 0\n',
        'requirements.txt': '',
        '.github/workflows/release.yml': 'name: Build macOS\n',
        'docs/releases/v0.1.2.md': 'Public release notes.\n',
        'docs/INSTALL.md': 'Installation instructions.\n',
        'docs/LumaView-Guide.pdf': '%PDF-1.4\n',
        '.gitignore': '.DS_Store\n__pycache__/\n.venv/\ndist/\n.env\n',
        'README.md': 'LumaView\n',
        'tests/example.py': '# test\n',
    }
    for name, content in contents.items():
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    (path / 'Install.command').chmod(0o755)
    return path


def test_archives_exclude_private_files_and_keep_installer_executable(tmp_path):
    spec = importlib.util.spec_from_file_location('package_sources', ROOT / 'scripts/package_sources.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = source_tree(tmp_path / 'source')
    for name in ['.env', '.DS_Store', '.venv/private', 'dist/old.zip', 'lumaview/__pycache__/cache.nbc']:
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('do not distribute')
    full, installer = module.package(source, source / 'dist')
    with ZipFile(full) as archive:
        assert 'LumaView/tests/example.py' in archive.namelist()
        assert 'LumaView/.github/workflows/release.yml' in archive.namelist()
        assert not any('private' in n or '.env' in n or 'cache' in n or '/dist/' in n for n in archive.namelist())
    with ZipFile(installer) as archive:
        assert not any('/tests/' in n or '/.github/' in n for n in archive.namelist())
        assert archive.read('LumaView/README.md') == b'Installation instructions.\n'
        assert (archive.getinfo('LumaView/Install.command').external_attr >> 16) & 0o111


@pytest.fixture
def publisher(tmp_path):
    if not all(shutil.which(tool) for tool in ('git', 'rsync', 'jq')):
        pytest.skip('Publication integration tests require git, rsync and jq.')
    source = source_tree(tmp_path / 'source')
    shutil.copy2(ROOT / 'scripts/publish_github.sh', source / 'scripts/publish_github.sh')
    env = dict(os.environ, GIT_AUTHOR_NAME='Release test', GIT_AUTHOR_EMAIL='test@example.invalid',
               GIT_COMMITTER_NAME='Release test', GIT_COMMITTER_EMAIL='test@example.invalid',
               GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='commit.gpgsign', GIT_CONFIG_VALUE_0='false')
    remote = tmp_path / 'remote.git'
    seed = tmp_path / 'seed'
    run('git', 'init', '--bare', '--initial-branch=main', str(remote), env=env)
    run('git', 'clone', str(remote), str(seed), env=env)
    (seed / '.gitignore').write_text('custom-output/\n')
    (seed / 'README.md').write_text('Created on GitHub\n')
    (seed / '.DS_Store').write_bytes(b'Finder metadata')
    run('git', 'add', '.', cwd=seed, env=env)
    run('git', 'commit', '-m', 'Initial repository', cwd=seed, env=env)
    run('git', 'push', 'origin', 'main', cwd=seed, env=env)
    initial = run('git', 'rev-parse', 'HEAD', cwd=seed, env=env).stdout.strip()
    commands = tmp_path / 'commands'
    commands.mkdir()
    mock = commands / 'gh'
    mock.write_text('#!' + sys.executable + '\n' + '''
import json, os, subprocess, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
args = sys.argv[1:]
env = os.environ
state = Path(env['LUMAVIEW_TEST_PUBLISHED']).parent
command = args[:2]
if command == ['auth', 'status']:
    pass
elif command == ['repo', 'view']:
    print('test/LumaView')
elif command == ['repo', 'clone']:
    subprocess.run(['git', 'clone', env['LUMAVIEW_TEST_REMOTE'], args[3]], check=True)
elif command == ['repo', 'edit']:
    if env.get('LUMAVIEW_TEST_TAG_RACE'):
        head = subprocess.check_output(['git', '--git-dir', env['LUMAVIEW_TEST_REMOTE'], 'rev-parse', 'main'], text=True).strip()
        subprocess.run(['git', '--git-dir', env['LUMAVIEW_TEST_REMOTE'], 'update-ref', 'refs/tags/v0.1.2', head], check=True)
elif args[0] == 'api':
    code = int(env.get('LUMAVIEW_TEST_API_EXIT', '0'))
    if code:
        print('GitHub API unavailable', file=sys.stderr)
        sys.exit(code)
    endpoint = urlsplit(args[1])
    if endpoint.path == 'repos/test/LumaView/releases':
        print(env.get('LUMAVIEW_TEST_DRAFT_STATE', 'true'))
        sys.exit(0)
    if endpoint.path == 'repos/test/LumaView/actions/runs/123':
        code = int(env.get('LUMAVIEW_TEST_STATE_API_EXIT', '0'))
        if code:
            print('HTTP 502: Server Error', file=sys.stderr)
            sys.exit(code)
        status = env.get('LUMAVIEW_TEST_RUN_STATUS', 'completed')
        default_conclusion = 'failure' if env.get('LUMAVIEW_TEST_BUILD_EXIT') == '1' else 'success'
        conclusion = env.get('LUMAVIEW_TEST_RUN_CONCLUSION', default_conclusion) if status == 'completed' else None
        query = args[args.index('--jq') + 1]
        subprocess.run(['jq', '-r', query], input=json.dumps(dict(status=status, conclusion=conclusion)), text=True, check=True)
        sys.exit(0)
    assert endpoint.path == 'repos/test/LumaView/actions/runs', args
    sha = parse_qs(endpoint.query)['head_sha'][0]
    def record(run_id, path='.github/workflows/release.yml', branch='v0.1.2', head=sha, event='push'):
        return dict(id=run_id, path=path, head_branch=branch, head_sha=head, event=event)
    # Higher IDs deliberately belong to the wrong workflow, ref or commit.
    runs = [record(9999, path='.github/workflows/tests.yml'),
            record(9998, branch='main', event='workflow_dispatch'),
            record(9997, head='0' * 40)]
    mode = env.get('LUMAVIEW_TEST_RUN_MODE', 'push')
    dispatched = (state / 'dispatched').exists()
    if mode == 'push' or (mode == 'dispatch' and dispatched):
        runs.append(record(123, event='workflow_dispatch' if dispatched else 'push'))
    query = args[args.index('--jq') + 1]
    subprocess.run(['jq', '-r', query], input=json.dumps({'workflow_runs': runs}), text=True, check=True)
elif command == ['workflow', 'run']:
    assert args[2] == 'release.yml' and args[args.index('--ref') + 1] == 'v0.1.2', args
    code = int(env.get('LUMAVIEW_TEST_DISPATCH_EXIT', '0'))
    if code:
        sys.exit(code)
    (state / 'dispatched').write_text(' '.join(args))
elif command == ['run', 'watch']:
    assert args[2] == '123', args
    counter = state / 'watch-count'
    count = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(count))
    if count <= int(env.get('LUMAVIEW_TEST_WATCH_ERRORS', '0')):
        print('failed to get jobs: HTTP 502: Server Error', file=sys.stderr)
        sys.exit(1)
    sys.exit(int(env.get('LUMAVIEW_TEST_BUILD_EXIT', '0')))
elif command == ['release', 'view']:
    if args[args.index('--json') + 1] == 'assets':
        assets = ['LumaView-0.1.2-macos-arm64.dmg', 'LumaView-0.1.2-macos-arm64.zip',
                  'LumaView-0.1.2-installer.zip', 'LumaView-0.1.2-github.zip',
                  'LumaView-Guide.pdf', 'SHA256SUMS.txt']
        for asset in assets:
            if asset != env.get('LUMAVIEW_TEST_MISSING_ASSET'):
                print(asset)
    else:
        print('true')
elif command == ['release', 'edit']:
    with Path(env['LUMAVIEW_TEST_PUBLISHED']).open('a') as output:
        print(' '.join(args), file=output)
else:
    raise AssertionError('Unexpected GitHub command: ' + repr(args))
''')
    mock.chmod(0o755)
    # Exercise bounded polling without spending real time waiting for a mock service.
    (commands / 'sleep').write_text('#!/bin/sh\nexit 0\n')
    (commands / 'sleep').chmod(0o755)
    env.update(PATH=str(commands) + os.pathsep + env['PATH'], LUMAVIEW_TEST_REMOTE=str(remote),
               LUMAVIEW_PUBLISH_ROOT=str(tmp_path / 'publish'),
               LUMAVIEW_TEST_PUBLISHED=str(tmp_path / 'published'))
    return source, remote, seed, env, initial


def publish(fixture, *options):
    source, _, _, env, _ = fixture
    return run('bash', str(source / 'scripts/publish_github.sh'), 'test/LumaView', *options, env=env, check=False)


def test_publish_preserves_remote_history_and_retries_without_new_commit(publisher):
    _, remote, _, env, initial = publisher
    result = publish(publisher)
    assert result.returncode == 0, result.stdout
    first = run('git', '--git-dir', str(remote), 'rev-parse', 'main').stdout.strip()
    run('git', '--git-dir', str(remote), 'merge-base', '--is-ancestor', initial, first)
    ignored = run('git', '--git-dir', str(remote), 'show', 'main:.gitignore').stdout
    assert 'custom-output/' in ignored and '.DS_Store' in ignored
    assert run('git', '--git-dir', str(remote), 'show', 'main:.DS_Store', check=False).returncode != 0
    assert Path(env['LUMAVIEW_TEST_PUBLISHED']).is_file()
    result = publish(publisher)
    assert result.returncode == 0, result.stdout
    assert run('git', '--git-dir', str(remote), 'rev-parse', 'main').stdout.strip() == first


def test_conflicting_tag_stops_before_push(publisher):
    _, remote, seed, env, initial = publisher
    run('git', 'tag', 'v0.1.2', cwd=seed, env=env)
    run('git', 'push', 'origin', 'v0.1.2', cwd=seed, env=env)
    result = publish(publisher)
    assert result.returncode != 0 and 'already points to another commit' in result.stdout
    assert run('git', '--git-dir', str(remote), 'rev-parse', 'main').stdout.strip() == initial
    assert not Path(env['LUMAVIEW_TEST_PUBLISHED']).exists()


def test_failed_build_does_not_publish(publisher):
    publisher[3]['LUMAVIEW_TEST_BUILD_EXIT'] = '1'
    result = publish(publisher)
    assert result.returncode != 0 and "Release workflow ended with 'failure'" in result.stdout
    assert not Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED']).exists()


def test_missing_tag_event_dispatches_release_on_the_same_tag(publisher):
    publisher[3]['LUMAVIEW_TEST_RUN_MODE'] = 'dispatch'
    result = publish(publisher)
    assert result.returncode == 0, result.stdout
    published = Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED'])
    assert published.exists()
    assert (published.parent / 'dispatched').exists()


def test_missing_dispatched_run_stops_without_publishing(publisher):
    publisher[3]['LUMAVIEW_TEST_RUN_MODE'] = 'never'
    result = publish(publisher)
    assert result.returncode != 0 and 'requested release workflow did not appear' in result.stdout
    assert not Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED']).exists()


def test_api_error_is_not_mistaken_for_a_missing_run(publisher):
    publisher[3]['LUMAVIEW_TEST_API_EXIT'] = '1'
    result = publish(publisher)
    assert result.returncode != 0 and 'Could not query GitHub Actions' in result.stdout
    published = Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED'])
    assert not published.exists()
    assert not (published.parent / 'dispatched').exists()


def test_dispatch_error_stops_without_publishing(publisher):
    publisher[3]['LUMAVIEW_TEST_RUN_MODE'] = 'dispatch'
    publisher[3]['LUMAVIEW_TEST_DISPATCH_EXIT'] = '1'
    result = publish(publisher)
    assert result.returncode != 0 and 'Could not start the release workflow' in result.stdout
    assert not Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED']).exists()


def test_incomplete_assets_do_not_publish(publisher):
    publisher[3]['LUMAVIEW_TEST_MISSING_ASSET'] = 'LumaView-0.1.2-macos-arm64.dmg'
    result = publish(publisher)
    assert result.returncode != 0 and 'Missing release asset' in result.stdout
    assert not Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED']).exists()


def test_watcher_http_error_does_not_hide_successful_build(publisher):
    publisher[3]['LUMAVIEW_TEST_WATCH_ERRORS'] = '1'
    result = publish(publisher)
    assert result.returncode == 0, result.stdout
    assert Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED']).exists()


def test_watcher_http_error_retries_a_running_build(publisher):
    publisher[3]['LUMAVIEW_TEST_WATCH_ERRORS'] = '1'
    publisher[3]['LUMAVIEW_TEST_RUN_STATUS'] = 'in_progress'
    result = publish(publisher)
    assert result.returncode == 0, result.stdout
    published = Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED'])
    assert published.exists()
    assert (published.parent / 'watch-count').read_text() == '2'


def test_persistent_monitoring_errors_do_not_report_build_failure(publisher):
    publisher[3]['LUMAVIEW_TEST_WATCH_ERRORS'] = '100'
    publisher[3]['LUMAVIEW_TEST_STATE_API_EXIT'] = '1'
    result = publish(publisher)
    assert result.returncode != 0 and 'Could not confirm the build state' in result.stdout
    assert 'Build failed' not in result.stdout and 'Release workflow ended' not in result.stdout
    published = Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED'])
    assert not published.exists()
    assert (published.parent / 'watch-count').read_text() == '6'


def test_explicit_draft_rebuild_moves_same_tag_and_preserves_history(publisher):
    _, remote, seed, env, initial = publisher
    run('git', 'tag', '-a', 'v0.1.2', '-m', 'Draft', cwd=seed, env=env)
    run('git', 'push', 'origin', 'v0.1.2', cwd=seed, env=env)
    result = publish(publisher, '--replace-draft-tag')
    assert result.returncode == 0, result.stdout
    head = run('git', '--git-dir', str(remote), 'rev-parse', 'main').stdout.strip()
    target = run('git', '--git-dir', str(remote), 'rev-list', '-n', '1', 'v0.1.2').stdout.strip()
    assert target == head and head != initial
    run('git', '--git-dir', str(remote), 'merge-base', '--is-ancestor', initial, head)
    assert Path(env['LUMAVIEW_TEST_PUBLISHED']).exists()


@pytest.mark.parametrize('draft_state', ['false', ''])
def test_draft_rebuild_rejects_published_or_missing_release(publisher, draft_state):
    _, remote, seed, env, initial = publisher
    run('git', 'tag', 'v0.1.2', cwd=seed, env=env)
    run('git', 'push', 'origin', 'v0.1.2', cwd=seed, env=env)
    env['LUMAVIEW_TEST_DRAFT_STATE'] = draft_state
    result = publish(publisher, '--replace-draft-tag')
    assert result.returncode != 0 and 'requires an existing unpublished draft' in result.stdout
    assert run('git', '--git-dir', str(remote), 'rev-parse', 'main').stdout.strip() == initial
    assert run('git', '--git-dir', str(remote), 'rev-parse', 'v0.1.2').stdout.strip() == initial


def test_draft_rebuild_does_not_overwrite_a_concurrently_changed_tag(publisher):
    _, remote, seed, env, initial = publisher
    run('git', 'tag', '-a', 'v0.1.2', '-m', 'Draft', cwd=seed, env=env)
    run('git', 'push', 'origin', 'v0.1.2', cwd=seed, env=env)
    env['LUMAVIEW_TEST_TAG_RACE'] = '1'
    result = publish(publisher, '--replace-draft-tag')
    assert result.returncode != 0 and 'stale info' in result.stdout
    assert run('git', '--git-dir', str(remote), 'rev-parse', 'v0.1.2').stdout.strip() == initial
    assert not Path(env['LUMAVIEW_TEST_PUBLISHED']).exists()

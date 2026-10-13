"""Exercise publication against local Git repositories, never GitHub."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
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
    if not shutil.which('git') or not shutil.which('rsync'):
        pytest.skip('Publication integration tests require git and rsync.')
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
    mock.write_text('''#!/bin/bash
set -eu
case "$1 $2" in
  'auth status') exit 0 ;;
  'repo view') echo test/LumaView ;;
  'repo clone') git clone "$LUMAVIEW_TEST_REMOTE" "$4" ;;
  'repo edit') exit 0 ;;
  'run list') echo 123 ;;
  'run watch') exit "${LUMAVIEW_TEST_BUILD_EXIT:-0}" ;;
  'release view')
    if [[ "$*" == *'--json assets'* ]]; then
      printf '%s\\n' LumaView-0.1.2-macos-arm64.dmg LumaView-0.1.2-macos-arm64.zip LumaView-0.1.2-installer.zip LumaView-0.1.2-github.zip LumaView-Guide.pdf SHA256SUMS.txt
    else echo true; fi ;;
  'release edit') printf '%s\\n' "$*" >> "$LUMAVIEW_TEST_PUBLISHED" ;;
  *) echo "Unexpected GitHub command: $*" >&2; exit 1 ;;
esac
''')
    mock.chmod(0o755)
    env.update(PATH=str(commands) + os.pathsep + env['PATH'], LUMAVIEW_TEST_REMOTE=str(remote),
               LUMAVIEW_PUBLISH_ROOT=str(tmp_path / 'publish'),
               LUMAVIEW_TEST_PUBLISHED=str(tmp_path / 'published'))
    return source, remote, seed, env, initial


def publish(fixture):
    source, _, _, env, _ = fixture
    return run('bash', str(source / 'scripts/publish_github.sh'), 'test/LumaView', env=env, check=False)


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
    assert result.returncode != 0 and 'Build failed' in result.stdout
    assert not Path(publisher[3]['LUMAVIEW_TEST_PUBLISHED']).exists()

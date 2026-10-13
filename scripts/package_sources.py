"""Package distributable sources and the smaller double-click installer."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

EXCLUDED = {'.git', '.venv', 'build', 'dist', '__pycache__', '.pytest_cache',
            '.ruff_cache', '.mypy_cache', 'htmlcov'}


def version(root: Path) -> str:
    tree = ast.parse((root / 'lumaview' / '__init__.py').read_text())
    return next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))


def source_files(root: Path):
    if (root / '.git').exists():
        paths = [Path(p) for p in subprocess.check_output(
            ['git', '-C', str(root), 'ls-files', '-z']).decode().split('\0') if p]
    else:
        paths = [p.relative_to(root) for p in root.rglob('*') if p.is_file()]
    for rel in sorted(paths):
        if any(p in EXCLUDED or p.startswith('.venv-') or p.endswith('.egg-info') for p in rel.parts):
            continue
        if rel.name in {'.DS_Store', '.coverage', '.env'} or rel.name.startswith('.env.'):
            continue
        if rel.suffix in {'.pyc', '.pyo', '.nbc', '.nbi', '.log', '.zip', '.dmg'}:
            continue
        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Release source must be a regular file: {rel}')
        yield rel


def package(root: Path, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    release = version(root)
    full = destination / f'LumaView-{release}-github.zip'
    installer = destination / f'LumaView-{release}-installer.zip'
    files = list(source_files(root))
    for required in ['Install.command', 'requirements.txt', 'lumaview/bilevel.py',
                     'scripts/bootstrap_macos.sh', 'docs/INSTALL.md', 'docs/LumaView-Guide.pdf']:
        if Path(required) not in files:
            raise ValueError(f'Missing release source: {required}')
    with ZipFile(full, 'w', ZIP_DEFLATED) as archive:
        for rel in files:
            archive.write(root / rel, Path('LumaView') / rel)
    with ZipFile(installer, 'w', ZIP_DEFLATED) as archive:
        for rel in files:
            include = rel.parts[0] in {'lumaview', 'assets', 'docs'} or str(rel) in {
                'Install.command', 'requirements.txt', 'scripts/bootstrap_macos.sh',
                'LICENSE', 'THIRD_PARTY.md'}
            if include:
                archive.write(root / rel, Path('LumaView') / rel)
        archive.write(root / 'docs/INSTALL.md', 'LumaView/README.md')
    for path in (full, installer):
        with ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise RuntimeError(f'Invalid archive: {path}')
            if not (archive.getinfo('LumaView/Install.command').external_attr >> 16) & 0o111:
                raise RuntimeError('Install.command must be executable before packaging.')
    return full, installer


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    output = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / 'dist'
    for path in package(root, output):
        print(path)

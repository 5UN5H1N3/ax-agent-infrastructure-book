"""Local teaching model of blueprint updates, path markers and acceptance.

Not an AX client, validator, runner or snapshot implementation. Only inline
files are modeled. No Git, bootstrap, models, secrets or network are used.
All writes in demo() are inside its own TemporaryDirectory.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import posixpath
from tempfile import TemporaryDirectory


@dataclass(frozen=True)
class Workspace:
    atespace: str
    name: str
    files: tuple[tuple[str, str], ...]

    def __post_init__(self):
        names = set()
        for name, _ in self.files:
            p = PurePosixPath(name)
            if not name or p.is_absolute() or '..' in p.parts or str(p) == '.':
                raise ValueError('toy files must stay inside the workspace')
            if str(p) in names:
                raise ValueError('duplicate toy file')
            names.add(str(p))


@dataclass(frozen=True)
class Binding:
    name: str
    path: str


def marker_name(path: str) -> str:
    """POSIX subset of AX MarkerName, intentionally without a spec digest."""
    clean = posixpath.normpath(path or '/workspace').strip('/') or 'root'
    return 'initialized-' + clean.replace('/', '-')


def preflight(atespace: str, bindings: tuple[Binding, ...], store: dict) -> list[str]:
    """Extra deployment policy, NOT upstream ValidateTask or full AX validation.

    Only checks binding existence in the same atespace, duplicate names,
    canonical paths under /workspace, overlapping paths and marker collisions.
    Lexical checks do not prove symlink/mount safety in a real guest.
    """
    errors = []
    names, paths, markers = set(), [], set()
    for binding in bindings:
        if (atespace, binding.name) not in store:
            errors.append('MISSING_WORKSPACE')
        if binding.name in names:
            errors.append('DUPLICATE_BINDING')
        names.add(binding.name)
        path = binding.path or '/workspace/' + binding.name
        canonical = posixpath.normpath(path)
        if not path.startswith('/workspace/') or path != canonical:
            errors.append('UNSAFE_PATH')
            continue
        if any(path == p or path.startswith(p + '/') or p.startswith(path + '/') for p in paths):
            errors.append('OVERLAPPING_PATHS')
        marker = marker_name(path)
        if marker in markers:
            errors.append('MARKER_COLLISION')
        paths.append(path)
        markers.add(marker)
    return errors


def setup_inline(root: Path, path: str, workspace: Workspace | None) -> str:
    """Toy setup subset: directory, marker check, inline files, marker write."""
    path = path or '/workspace'
    canonical = posixpath.normpath(path)
    if path != canonical or not (path == '/workspace' or path.startswith('/workspace/')):
        raise ValueError('toy setup path must be canonical and inside /workspace')
    target = root / path.lstrip('/')
    target.mkdir(parents=True, exist_ok=True)
    state = root / 'ax'
    state.mkdir(exist_ok=True)
    marker = state / marker_name(path)
    if marker.exists():
        return 'SKIPPED'
    if workspace is not None:
        for name, content in workspace.files:
            file = target / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding='utf-8')
    marker.write_text('workspace: ' + (workspace.name if workspace else 'unknown'), encoding='utf-8')
    return 'SETUP_COMPLETE'


def acceptance(target: Path, expected: Workspace) -> str:
    """Read-only fixture check, not a complete environment acceptance probe."""
    for name, content in expected.files:
        file = target / name
        if not file.is_file():
            return 'ENVIRONMENT_DRIFT'
        if sha256(file.read_bytes()).digest() != sha256(content.encode()).digest():
            return 'ENVIRONMENT_DRIFT'
    return 'ACCEPT'


def demo() -> dict:
    with TemporaryDirectory(prefix='ax-ch17-') as tmp:
        root = Path(tmp)
        a, b, missing = (root / n for n in ['task-a', 'task-b', 'task-missing'])
        for task in (a, b, missing):
            task.mkdir()
        v1 = Workspace('team-a', 'checkout-v1', (('toolchain.lock', 'v1\n'),))
        v2_same_name = Workspace('team-a', 'checkout-v1', (('toolchain.lock', 'v2\n'),))
        v2_new_name = Workspace('team-a', 'checkout-v2', v2_same_name.files)
        bindings = (Binding('checkout-v1', '/workspace/service'),)
        store = {('team-a', 'checkout-v1'): v1}
        assert not preflight('team-a', bindings, store)
        setup_inline(a, bindings[0].path, v1)
        target_a = a / 'workspace/service'
        (target_a / 'notes.txt').write_text('keep my work\n', encoding='utf-8')
        store[('team-a', 'checkout-v1')] = v2_same_name
        # Even explicitly supplying the latest blueprint does not bypass marker.
        repeated_setup = setup_inline(a, bindings[0].path, store[('team-a', 'checkout-v1')])
        store[('team-a', 'checkout-v2')] = v2_new_name
        assert not preflight('team-a', (Binding('checkout-v2', '/workspace/service'),), store)
        setup_inline(b, '/workspace/service', v2_new_name)
        missing_errors = preflight('team-a', (Binding('absent', '/workspace/service'),), store)
        unchecked = setup_inline(missing, '/workspace/service', None)
        return {
            'source': 'local_inline_file_model',
            'ax_and_substrate_contacted': False,
            'existing_setup': repeated_setup,
            'existing_file': (target_a / 'toolchain.lock').read_text().strip(),
            'existing_acceptance_for_v2': acceptance(target_a, v2_same_name),
            'existing_notes_preserved': (target_a / 'notes.txt').read_text() == 'keep my work\n',
            'new_task_file': (b / 'workspace/service/toolchain.lock').read_text().strip(),
            'new_task_acceptance': acceptance(b / 'workspace/service', v2_new_name),
            'missing_reference_preflight': missing_errors,
            'missing_reference_unchecked_setup': unchecked,
        }


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2, ensure_ascii=False))

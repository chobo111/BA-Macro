"""On-demand profile file discovery and bounded recent history."""
from pathlib import Path
from domain import read_json


def recent_profiles(paths, current):
    """Keep a small ordered history without changing or scanning other folders."""
    result = []
    for value in ([current] if current is not None else []) + list(paths):
        path = Path(value).resolve()
        if path not in result:
            result.append(path)
    return [str(path) for path in result[:10]]


def profile_entries(directory, current, recent):
    """Refresh only on demand; malformed unrelated JSON is not a profile entry."""
    result, seen = [], set()
    def add(path, group):
        path = Path(path).resolve()
        if path in seen or not path.is_file() or path.suffix.lower() != '.json':
            return
        try:
            data = read_json(path)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or not isinstance(data.get('macros'), list):
            return
        seen.add(path)
        result.append((path, group))
    if current is not None:
        add(current, '최근 사용')
    for path in recent:
        add(path, '최근 사용')
    for path in sorted(Path(directory).glob('*.json'), key=lambda path: path.name.casefold()):
        add(path, '이 폴더')
    return result

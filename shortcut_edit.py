"""Named shortcut checks and immutable, previewable row edits."""
from dataclasses import dataclass

from hotkeys import Hotkeys, binding_conflict
from shortcuts import ShortcutError


def key_label(key):
    names = {'ctrl': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift', 'win': 'Win', 'plus': '+'}
    return '+'.join(names.get(part, part.upper()) for part in key.split('+')) if key else '공란'


def row_label(index, row):
    memo = ' '.join(row.memo.split())
    memo = memo[:24] + '…' if len(memo) > 24 else memo
    return f'{index + 1}행' + (f' · {memo}' if memo else '')


@dataclass(frozen=True)
class ShortcutEdit:
    # Each change contains the row ID, expected old key and proposed new key.
    changes: tuple
    message: str = ''
    action: str = '확인'


@dataclass(frozen=True)
class _Owner:
    row_id: str | None
    label: str
    binding: object


def _globals(keys):
    bindings = Hotkeys.validated_bindings((key, label, '')
        for label, key in zip(('좌표 캡처', '시작·정지'), keys))
    return [_Owner(None, binding.action, binding) for binding in bindings]


def _rows(rows):
    owners = []
    for index, row in enumerate(rows):
        try:
            binding = Hotkeys.compile(row.move_hk, row=True)
        except ValueError:
            continue  # Unrelated imported errors must not prevent repairs.
        if binding is not None:
            owners.append(_Owner(row.id, row_label(index, row), binding))
    return owners


def _conflicts(binding, owners):
    if binding is None:
        return []
    return [(owner, reason) for owner in owners if owner.binding is not None
            and (reason := binding_conflict(binding, owner.binding))]


def _conflict_message(conflicts):
    lines = [f'{owner.label} ({key_label(owner.binding.key)}): {reason}'
             for owner, reason in conflicts[:3]]
    if len(conflicts) > 3:
        lines.append(f'외 {len(conflicts) - 3}개 항목과 충돌합니다.')
    if any(owner.row_id is None for owner, _ in conflicts):
        lines.append('환경설정에서 전역 단축키를 변경하세요.')
    return '\n'.join(lines)


def plan_row_shortcut(rows, global_keys, row_id, value):
    row = next((row for row in rows if row.id == row_id), None)
    if row is None:
        raise ShortcutError('편집할 행이 없습니다. 다시 선택하세요.')
    binding = Hotkeys.compile(value, row=True)
    value = binding.key if binding is not None else ''
    change = (row_id, row.move_hk, value)
    owners = _globals(global_keys) + _rows(rows)
    conflicts = _conflicts(binding, (owner for owner in owners if owner.row_id != row_id))
    if not conflicts:
        return ShortcutEdit((change,))
    message = _conflict_message(conflicts)
    if len(conflicts) != 1:
        raise ShortcutError(message + '\n여러 항목이 충돌하므로 먼저 중복을 정리하세요.')
    other = conflicts[0][0]
    if other.row_id is None or not binding.overlaps(other.binding):
        raise ShortcutError(message)

    partner = next(row for row in rows if row.id == other.row_id)
    try:
        replacement_binding = Hotkeys.compile(row.move_hk, row=True)
        replacement = replacement_binding.key if replacement_binding is not None else ''
    except ValueError as error:
        raise ShortcutError(message + '\n기존 단축키를 교환할 수 없습니다: ' + str(error)) from error
    changes = (change, (partner.id, partner.move_hk, replacement))
    proposed = [owner for owner in owners if owner.row_id not in (row_id, partner.id)]
    proposed.extend((_Owner(row_id, '', binding), _Owner(partner.id, '', replacement_binding)))
    for changed_id, _, _ in changes:
        candidate = next(owner.binding for owner in proposed if owner.row_id == changed_id)
        remaining = _conflicts(candidate, (owner for owner in proposed if owner.row_id != changed_id))
        if remaining:
            raise ShortcutError(message + '\n교환 후에도 충돌이 남습니다. 다른 단축키를 지정하세요.')
    labels = {item.id: row_label(index, item) for index, item in enumerate(rows)}
    preview = '\n'.join(f'{labels[rid]}: {key_label(old)} → {key_label(new)}' for rid, old, new in changes)
    return ShortcutEdit(changes, f'{rows.index(partner) + 1}행의 단축키와 중복됩니다.\n\n{preview}',
                        '서로 바꾸기' if replacement else '옮기기')


def validate_global_shortcuts(keys, rows, prepared):
    """Protect usable rows while allowing settings to repair disabled imports."""
    row_owners = [_Owner(row.id, row_label(index, row), binding)
                  for index, (row, binding) in enumerate(zip(rows, prepared.rows))
                  if binding is not None]
    for owner in _globals(keys):
        conflicts = _conflicts(owner.binding, row_owners)
        if conflicts:
            raise ShortcutError(f'{owner.label}:\n' + _conflict_message(conflicts))

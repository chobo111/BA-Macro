"""Profiles and settings. No GUI or input hooks are imported here."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import os
from pathlib import Path
import tempfile
import uuid
from shortcuts import hotkey, validate_hotkeys, ShortcutError
from script_language import coordinate_tokens

MODES = {
    "move_only": "커서 이동",
    "script": "스크립트",
}
PROFILE_FORMAT = 'ba-macro-profile-v1'


class WindowSizeUnavailable(OSError):
    """A minimized/restoring window has no usable client-size sample yet."""
    def __init__(self):
        super().__init__('앱이 최소화되었거나 복원 중입니다. 창을 복원한 뒤 다시 시도하세요.')


class WindowPositionChanged(RuntimeError):
    def __init__(self):
        super().__init__('앱 창 위치가 변경되었습니다. 위치를 확인한 뒤 다시 시작하세요.')


def target_identity(data):
    """Validate the current filename-based target identity."""
    if not data:
        return {}
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        raise ValueError('대상 앱 정보가 올바르지 않습니다.')
    exe = data.get('exe', '')
    if any(character in exe for character in '/\\:'):
        raise ValueError('대상 앱 exe에는 경로가 아닌 실행파일명만 입력하세요.')
    return {'exe': exe,
            'class': data.get('class', ''), 'title': data.get('title', '')}


def same_program(saved, current):
    """Match the executable/class, allowing titles such as FPS counters to change."""
    if not saved or not current:
        return False
    if saved.get('exe'):
        return (current.get('exe', '').casefold() == saved['exe'].casefold()
                and current.get('class', '') == saved.get('class', ''))
    return bool(saved.get('class') and saved.get('title')) and all(
        current.get(key) == saved[key] for key in ('class', 'title'))


def bounded_int(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}: 숫자가 필요합니다.")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: 올바른 정수를 입력하세요.") from None
    if str(value).strip() != str(result) and not isinstance(value, int):
        raise ValueError(f"{label}: 정수를 입력하세요.")
    if not low <= result <= high:
        raise ValueError(f"{label}: {low}~{high} 범위여야 합니다.")
    return result


@dataclass
class Row:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    move_hk: str = ""
    x: int = 0
    y: int = 0
    memo: str = ""
    action_mode: str = "move_only"
    script: str = ""

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict):
            raise ValueError("매크로 행 형식이 올바르지 않습니다.")
        unknown = set(data) - {"id", "move_hk", "coords", "memo", "action_mode", "script"}
        if unknown:
            raise ValueError(f"지원하지 않는 매크로 항목: {', '.join(sorted(unknown))}")
        if not isinstance(data.get("coords"), str):
            raise ValueError("coords에 x, y 형식의 좌표 문자열이 필요합니다.")
        coords = data["coords"].split(",")
        if len(coords) != 2:
            raise ValueError("좌표는 x, y 형식이어야 합니다.")
        x, y = [v.strip() for v in coords]
        mode = data.get("action_mode")
        script = data.get("script", "")
        if not isinstance(script, str) or len(script) > 100000:
            raise ValueError("스크립트는 100,000자 이하의 문자열이어야 합니다.")
        if mode not in MODES:
            raise ValueError(f"지원하지 않는 실행 모드: {mode}")
        return cls(str(data.get("id") or uuid.uuid4().hex), hotkey(data.get("move_hk", "")),
                   bounded_int(x, -99999, 99999, "X"),
                   bounded_int(y, -99999, 99999, "Y"), str(data.get("memo", "")), mode, script)


@dataclass
class Profile:
    rows: list[Row] = field(default_factory=list)
    resolution: tuple[int, int] | None = None
    target_program: dict = field(default_factory=dict)

    @classmethod
    def default(cls, reserved=()):
        # Keep three presets, leaving only keys owned by global shortcuts blank.
        reserved = {hotkey(key) for key in reserved}
        return cls([Row(move_hk="" if k in reserved else k)
                    for k in ("q", "w", "e")])

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("macros"), list):
            raise ValueError("macros 목록이 있는 JSON 프로필이 필요합니다.")
        if data.get('format') != PROFILE_FORMAT:
            raise ValueError(f'현재 프로필 형식(format: {PROFILE_FORMAT})만 지원합니다.')
        if not 1 <= len(data["macros"]) <= 200:
            raise ValueError("프로필은 1~200행이어야 합니다.")
        rows = [Row.read(d) for d in data["macros"]]
        if len({r.id for r in rows}) != len(rows):
            raise ValueError("중복된 행 ID가 있습니다.")
        resolution = data.get("target_resolution", "알 수 없음")
        size = None
        if resolution != "알 수 없음":
            if not isinstance(resolution, str) or len(resolution.split("x")) != 2:
                raise ValueError("해상도는 1920x1080 형식 또는 '알 수 없음'이어야 합니다.")
            values = resolution.split("x")
            size = tuple(bounded_int(v.strip(), 1, 100000, "해상도") for v in values)
        obj = cls(rows, size, target_identity(data.get('target_program')))
        obj.validate()
        return obj

    def has_fixed_coordinates(self):
        # Keep hidden coordinates too: changing a row's mode can restore them.
        # The default table point (0, 0) alone does not establish a saved basis.
        return any(row.x or row.y or coordinate_tokens(row.script) for row in self.rows)

    def validate(self, reserved=()):
        validate_hotkeys((*reserved, *(row.move_hk for row in self.rows)))

    def export(self):
        return {"format": PROFILE_FORMAT, "target_resolution": "%dx%d" % self.resolution if self.resolution else "알 수 없음",
                "target_program": target_identity(self.target_program),
                "macros": [{"id": r.id, "move_hk": r.move_hk,
                            "coords": f"{r.x}, {r.y}", "memo": r.memo, "action_mode": r.action_mode,
                            "script": r.script} for r in self.rows]}


@dataclass
class Settings:
    capture_hk: str = "ctrl+s"
    pause_hk: str = "ctrl+`"
    click_hold_ms: int = 10
    resize_convert_profile: bool = True
    drag_segment_duration_ms: int = 10
    exclusive_hook: bool = True
    auto_pause: bool = False
    always_run_as_admin: bool = False
    auto_convert_on_start: bool = False
    theme: str = "System"
    last_profile: str = ""

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict):
            raise ValueError("설정 형식이 잘못되었습니다.")
        # Discard the removed menu history while preserving other settings.
        data = {key: value for key, value in data.items() if key != "recent_profiles"}
        unknown = set(data) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError(f"지원하지 않는 설정 항목: {', '.join(sorted(unknown))}")
        obj = cls()
        for name in ("capture_hk", "pause_hk"):
            setattr(obj, name, hotkey(data.get(name, getattr(obj, name))))
        if not obj.pause_hk or not obj.capture_hk or obj.pause_hk == obj.capture_hk:
            raise ShortcutError("캡처와 작동/정지 단축키는 서로 다르게 지정하세요.")
        validate_hotkeys((obj.pause_hk, obj.capture_hk))
        for name in ("click_hold_ms", "drag_segment_duration_ms"):
            setattr(obj, name, bounded_int(data.get(name, getattr(obj, name)), 1 if name == "click_hold_ms" else 0, 99999, name))
        for name in ("exclusive_hook", "auto_pause", "resize_convert_profile", "auto_convert_on_start", "always_run_as_admin"):
            value = data.get(name, getattr(obj, name))
            if not isinstance(value, bool):
                raise ValueError(f"{name}: 참/거짓 값이 필요합니다.")
            setattr(obj, name, value)
        obj.theme = data.get("theme", obj.theme)
        if obj.theme not in ("Dark", "Light", "System"):
            raise ValueError("테마 값이 올바르지 않습니다.")
        obj.last_profile = str(data.get("last_profile", ""))
        return obj


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)

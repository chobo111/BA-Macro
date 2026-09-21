"""Profiles and settings. No GUI or input hooks are imported here."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import tempfile
import uuid

MODES = {
    "move_and_click": "좌표 클릭",
    "move_only": "커서 이동",
    "script": "스크립트 실행",
}


def hotkey(value: str) -> str:
    aliases = {"control": "ctrl", "windows": "win", "meta": "win", "escape": "esc", "return": "enter"}
    parts = [aliases.get(p.strip().lower(), p.strip().lower()) for p in str(value).split("+") if p.strip()]
    if not parts:
        return ""
    if len(set(parts)) != len(parts):
        raise ValueError("단축키에 같은 키가 두 번 들어갔습니다.")
    mods = [k for k in ("ctrl", "alt", "shift", "win") if k in parts]
    keys = [k for k in parts if k not in mods]
    if len(keys) != 1:
        raise ValueError("단축키는 보조키와 일반 키 하나로 지정하세요.")
    return "+".join(mods + keys)


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
        if "coords" in data:
            coords = str(data["coords"]).split(",")
            if len(coords) != 2:
                raise ValueError("좌표는 x, y 형식이어야 합니다.")
            x, y = [v.strip() for v in coords]
        else:
            x, y = data.get("x", 0), data.get("y", 0)
        mode = data.get("action_mode", "move_only")
        mode = "move_only" if mode in ("default", "target_only") else mode
        script = data.get("script", "")
        if not isinstance(script, str) or len(script) > 100000:
            raise ValueError("스크립트는 100,000자 이하의 문자열이어야 합니다.")
        if mode == 'target_and_return_click':
            mode = 'script'
            script = f'// 이전 스킬 사용 동작\nclick {int(x)},{int(y)}\nsleep 15\nclick mouse'
        if mode not in MODES:
            raise ValueError(f"지원하지 않는 실행 모드: {mode}")
        return cls(str(data.get("id") or uuid.uuid4().hex), hotkey(data.get("move_hk", "")),
                   bounded_int(x, -99999, 99999, "X"),
                   bounded_int(y, -99999, 99999, "Y"), str(data.get("memo", "")), mode, script)


@dataclass
class Profile:
    rows: list[Row] = field(default_factory=list)
    resolution: tuple[int, int] | None = None

    @classmethod
    def default(cls):
        return cls([Row(move_hk=k, action_mode="move_and_click") for k in ("q", "w", "e")])

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("macros"), list):
            raise ValueError("macros 목록이 있는 JSON 프로필이 필요합니다.")
        if not 1 <= len(data["macros"]) <= 200:
            raise ValueError("프로필은 1~200행이어야 합니다.")
        rows = [Row.read(d) for d in data["macros"]]
        # Older coordinate-click mode restored the cursor. Preserve it as an explicit script.
        if data.get('version', 1) < 3:
            for row, old in zip(rows, data['macros']):
                if old.get('action_mode') == 'move_and_click':
                    row.action_mode = 'script'
                    row.script = f'click {row.x},{row.y}\nsleep 15\nmove mouse'
        if len({r.id for r in rows}) != len(rows):
            raise ValueError("중복된 행 ID가 있습니다.")
        resolution = data.get("target_resolution", "알 수 없음")
        size = None
        if isinstance(resolution, str) and "x" in resolution:
            values = resolution.lower().split("x")
            if len(values) != 2:
                raise ValueError("해상도 형식이 올바르지 않습니다.")
            size = tuple(bounded_int(v.strip(), 1, 100000, "해상도") for v in values)
        obj = cls(rows, size)
        obj.validate()
        return obj

    def validate(self, reserved=()):
        used = {hotkey(v) for v in reserved if v}
        for row in self.rows:
            for value in (row.move_hk,):
                if not value:
                    continue
                normalized = hotkey(value)
                if normalized in used:
                    raise ValueError(f"중복된 단축키: {normalized.upper()}")
                used.add(normalized)

    def export(self):
        return {"version": 3, "target_resolution": "%dx%d" % self.resolution if self.resolution else "알 수 없음",
                "macros": [{"id": r.id, "move_hk": r.move_hk,
                            "coords": f"{r.x}, {r.y}", "memo": r.memo, "action_mode": r.action_mode,
                            "script": r.script} for r in self.rows]}

    def convert(self, source, destination):
        sw, sh, dw, dh = [bounded_int(v, 1, 100000, "해상도") for v in (*source, *destination)]
        coords = [(round(r.x * dw / sw), round(r.y * dh / sh)) for r in self.rows]
        if any(abs(v) > 99999 for pair in coords for v in pair):
            raise ValueError("변환 결과가 좌표 범위를 초과합니다.")
        for row, (x, y) in zip(self.rows, coords):
            row.x, row.y = x, y
        self.resolution = (dw, dh)


@dataclass
class Settings:
    capture_hk: str = "ctrl+s"
    pause_hk: str = "ctrl+tab"
    click_delay: int = 15
    click_duration: int = 50
    curve_duration: int = 10
    return_delay: int = 15
    after_action_delay: int = 30
    auto_fire: bool = True
    exclusive_hook: bool = True
    show_realtime: bool = True
    theme: str = "Dark"
    last_profile: str = ""

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict):
            raise ValueError("설정 형식이 잘못되었습니다.")
        obj = cls()
        for name in ("capture_hk", "pause_hk"):
            setattr(obj, name, hotkey(data.get(name, getattr(obj, name))))
        if not obj.pause_hk or not obj.capture_hk or obj.pause_hk == obj.capture_hk:
            raise ValueError("캡처와 작동/정지 단축키는 서로 다르게 지정하세요.")
        for name in ("click_delay", "click_duration", "curve_duration", "return_delay", "after_action_delay"):
            setattr(obj, name, bounded_int(data.get(name, getattr(obj, name)), 1 if name == "click_duration" else 0, 10000, name))
        for name in ("auto_fire", "exclusive_hook", "show_realtime"):
            value = data.get(name, getattr(obj, name))
            if not isinstance(value, bool):
                raise ValueError(f"{name}: 참/거짓 값이 필요합니다.")
            setattr(obj, name, value)
        obj.theme = data.get("theme", "Dark")
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

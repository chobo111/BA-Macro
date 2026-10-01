"""Application releases; independent of the profile JSON schema version."""
APP_NAME = 'Blue Archive Studio'
RELEASES = (
    ('0.2.0-experimental', '2026-10-01', (
        '정보 메뉴에 버전별 변경 기록 추가',
        '스크립트용 독립 터치 입력 실험 옵션 추가 (대상 창 선택 필요)',
        '일반 마우스 방식과 전환 가능, 좌표 클릭·커서 이동 동작은 기존 방식 유지',
        '독립 터치는 좌클릭·드래그 전용이며 게임별 입력 지원 확인 필요',
    )),
    ('0.1.0-refactor', '이전 기준', (
        '프로필·실행 상태 분리 및 자동저장 최적화',
        '스크립트 편집기와 공통 좌표 표시, 시작·정지 버튼 개선',
        '현재 JSON 형식 지원 및 구형 자동 변환 제거',
    )),
)
VERSION = RELEASES[0][0]

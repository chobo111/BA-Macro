"""On-demand Korean reference for the supported macro language."""
from html import escape
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton
from script_language import KEYS, ALIASES


COMMAND_HELP = [
    ('move X,Y', '지정 좌표로 실제 커서를 이동합니다. mouse도 사용할 수 있습니다.'),
    ('click X,Y [X,Y ...]', '각 좌표로 이동해 왼쪽 버튼을 한 번씩 클릭합니다. 클릭 유지 시간은 환경설정을 따릅니다. click mouse도 가능합니다.'),
    ('press X,Y', '좌표로 이동해 왼쪽 버튼을 누릅니다.'),
    ('release X,Y', '좌표로 이동해 왼쪽 버튼을 뗍니다.'),
    ('switch X,Y', '좌표로 이동해 왼쪽 버튼의 누름/해제를 전환합니다.'),
    ('mouse_press 버튼', '커서를 이동하지 않고 지정 버튼을 누릅니다.'),
    ('mouse_release 버튼', '커서를 이동하지 않고 지정 버튼을 뗍니다.'),
    ('mouse_switch 버튼', '커서를 이동하지 않고 지정 버튼의 누름/해제를 전환합니다.'),
    ('key_press 키', '키 하나를 누릅니다. 조합 입력은 key_press ctrl 다음 key_press a처럼 나눠 작성합니다.'),
    ('key_release 키', '키 하나를 뗍니다.'),
    ('key_switch 키', '키 하나의 누름/해제를 전환합니다.'),
    ('text "문자열"', '큰따옴표로 감싼 문자열을 입력합니다. JSON 문자열 표기(예: \\n, \\" )를 지원하며 최대 10,000자입니다.'),
    ('sleep 시간', '지정한 밀리초(ms)만큼 기다립니다. 1,000ms = 1초입니다.'),
    ('curve X,Y X,Y [X,Y ...]', '첫 좌표에서 왼쪽 버튼을 누른 채 나머지 좌표를 차례로 드래그합니다. 최소 두 좌표가 필요하며 mouse를 섞어 쓸 수 있습니다. 구간당 이동 시간은 환경설정의 드래그 이동 시간을 따릅니다.'),
    ('curve_first_time 시간', '이후 curve에서 시작 좌표를 누른 뒤 이동하기 전까지의 대기 시간을 지정합니다. 스크립트 시작 시 기본 0ms입니다.'),
    ('curve_last_time 시간 | untilcmd', '이후 curve의 마지막 좌표에서 버튼을 떼기 전 대기 시간을 지정합니다. 기본 0ms이며 untilcmd는 드래그 직후 버튼을 떼지 않습니다. curve_release 등으로 해제할 수 있으며 일반 누름은 스크립트 종료 시 해제됩니다.'),
    ('curve_release', '드래그 중인 왼쪽 버튼을 뗍니다. 인수는 없습니다.'),
    ('release_all', '이 매크로가 누르고 있는 키와 마우스 버튼을 모두 해제합니다. 인수는 없습니다.'),
    ('loop [횟수]', '횟수를 생략하면 실행 단축키를 누르는 동안 반복합니다. 횟수는 1~10,000이며 loop_end와 짝을 이뤄야 합니다. 빈 루프는 허용하지 않으며 최대 8단계로 중첩할 수 있습니다.'),
    ('loop_end', '가장 안쪽 loop를 끝냅니다. 키를 놓으면 진행 중인 반복 회차를 마친 뒤 멈춥니다. 작동 중지는 대기도 중단합니다.'),
    ('release_actions', '이 줄 이후 명령은 앞부분 실행이 끝나고 실행 단축키를 놓은 뒤 실행합니다. 루프 밖에서 한 번만 작성하며, 이 구역의 loop에는 반드시 횟수를 지정합니다.'),
]


def help_html():
    commands = ''.join(f'<h3><code>{escape(syntax)}</code></h3><p>{escape(description)}</p>'
                       for syntax, description in COMMAND_HELP)
    return '''<h2>스크립트 작성 도움말</h2>
<p>한 줄에 명령 하나를 작성합니다. 명령어의 대소문자는 구분하지 않습니다.
빈 줄은 무시하며 // 뒤는 주석입니다. 따옴표 안의 //는 문자열 그대로 입력합니다.</p>
<p><b>X,Y</b>는 설명용 표기입니다. 실제 숫자(예: 1200,800)로 바꿔야 저장할 수 있습니다.
좌표는 각 -99999~99999이며 대상 영역 안에 있어야 합니다. 대상 창을 선택하면 창 내부 좌표,
전체화면이면 화면 좌표를 사용합니다. 한 명령에 최대 256개 좌표를 쓸 수 있습니다.</p>
<p><b>mouse</b>는 스크립트 실행을 시작할 때의 커서 위치입니다. 루프마다 다시 읽지 않습니다.
편집기와 메인 창 밖에서 우클릭하면 현재 입력 위치에 x,y가 추가됩니다.</p>
<p>시간은 0~600,000ms 정수입니다. 스크립트 전체 길이는 최대 100,000자입니다.
예제를 선택하면 기존 내용을 덮어씁니다. 저장 시 문법을 검사하며 오류는 잠시 로그로 표시합니다.</p>
<h2>명령어</h2>''' + commands + '''
<h2>누름 유지와 해제</h2>
<p>일반 press/key_press/mouse_press로 누른 입력은 정상 종료 시 해제됩니다.
switch/key_switch/mouse_switch로 켠 입력은 정상 종료 뒤에도 유지될 수 있습니다.
해당 release 명령 또는 release_all로 해제하세요. 작동 중지·오류·대상 창 변경 등에서도 해제됩니다.</p>
<h2>마우스 버튼</h2><p>left, right, middle, back, forward.
별칭: backward 또는 x1 = back, x2 = forward. 버튼 이름은 소문자로 입력하세요.</p>
<h2>지원 키</h2><p>''' + escape(', '.join(KEYS)) + '</p><p>별칭: ' + escape(
        ', '.join(f'{alias} = {key}' for alias, key in ALIASES.items())) + '''</p>
<h2>예시</h2><pre>loop
curve_first_time 5
curve_last_time 5
curve 1200,800 mouse
sleep 5
loop_end</pre>
<p>1200,800을 실제 스킬 카드 좌표로 바꾸면 해당 카드에서 시작 위치 mouse까지 반복 드래그합니다.</p>
<p>컨트롤러/게임패드와 MuMu 전용 조준 명령은 지원하지 않습니다.
loop until_release는 삭제된 문법이며 loop만 사용합니다.</p>'''


class ScriptHelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('스크립트 도움말')
        area = parent.screen().availableGeometry()
        self.resize(min(620, area.width()), min(600, area.height()))
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setStyleSheet('QTextBrowser { background: #0e1822; color: #e8f1fa; border: 1px solid #33495d; padding: 10px; }')
        browser.setHtml(help_html())
        layout.addWidget(browser)
        close = QPushButton('닫기')
        close.clicked.connect(self.accept)
        layout.addWidget(close)

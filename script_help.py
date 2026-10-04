"""On-demand Korean reference for the supported macro language."""
from html import escape
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton


COMMAND_HELP = [
    ('move X,Y', '가상 터치 위치를 지정 좌표로 옮깁니다. 실제 커서는 움직이지 않으며 mouse도 사용할 수 있습니다.'),
    ('click X,Y [X,Y ...]', '각 좌표로 이동해 왼쪽 버튼을 한 번씩 클릭합니다. 클릭 유지 시간은 환경설정을 따릅니다. click mouse도 가능합니다.'),
    ('press X,Y', '좌표로 이동해 왼쪽 버튼을 누릅니다.'),
    ('release X,Y', '좌표로 이동해 왼쪽 버튼을 뗍니다.'),
    ('switch X,Y', '좌표로 이동해 왼쪽 버튼의 누름/해제를 전환합니다.'),
    ('mouse_press left', '현재 가상 터치 위치를 누릅니다.'),
    ('mouse_release left', '현재 가상 터치를 뗍니다.'),
    ('mouse_switch left', '현재 가상 터치의 누름/해제를 전환합니다.'),
    ('sleep 시간', '지정한 밀리초(ms)만큼 기다립니다. 1,000ms = 1초입니다.'),
    ('curve X,Y X,Y [X,Y ...]', '첫 좌표에서 왼쪽 버튼을 누른 채 나머지 좌표를 차례로 드래그합니다. 최소 두 좌표가 필요하며 mouse를 섞어 쓸 수 있습니다. 구간당 이동 시간은 환경설정의 curve 드래그 시간을 따릅니다.'),
    ('curve_first_time 시간', '이후 curve에서 시작 좌표를 누른 뒤 이동하기 전까지의 대기 시간을 지정합니다. 스크립트 시작 시 기본 0ms입니다.'),
    ('curve_last_time 시간 | untilcmd', '이후 curve의 마지막 좌표에서 버튼을 떼기 전 대기 시간을 지정합니다. 기본 0ms이며 untilcmd는 드래그 직후 버튼을 떼지 않습니다. curve_release 등으로 해제할 수 있으며 일반 누름은 스크립트 종료 시 해제됩니다.'),
    ('curve_release', '드래그 중인 왼쪽 버튼을 뗍니다. 인수는 없습니다.'),
    ('release_all', '이 매크로가 누르고 있는 터치를 해제합니다. 인수는 없습니다.'),
    ('loop [횟수]', '횟수를 생략하면 실행 단축키를 누르는 동안 반복합니다. 횟수는 1~10,000이며 loop_end와 짝을 이뤄야 합니다. 빈 루프는 허용하지 않으며 최대 8단계로 중첩할 수 있습니다.'),
    ('loop_end', '가장 안쪽 loop를 끝냅니다. 키를 놓으면 진행 중인 반복 회차를 마친 뒤 멈춥니다. 작동 중지는 대기도 중단합니다.'),
    ('release_actions', '이 줄 이후 명령은 앞부분 실행이 끝나고 실행 단축키를 놓은 뒤 실행합니다. 루프 밖에서 한 번만 작성하며, 이 구역의 loop에는 반드시 횟수를 지정합니다.'),
]


def help_html():
    commands = ''.join(f'<h3><code>{escape(syntax)}</code></h3><p>{escape(description)}</p>'
                       for syntax, description in COMMAND_HELP)
    return '''<h2>스크립트 작성 도움말</h2>
<p>한 줄에 명령 하나를 작성합니다. 명령어의 대소문자는 구분하지 않습니다.
빈 줄은 무시하며 // 뒤는 주석입니다.</p>
<p><b>X,Y</b>는 설명용 표기입니다. 실제 숫자(예: 1200,800)로 바꿔야 저장할 수 있습니다.
좌표는 각 -99999~99999이며 선택한 대상 창 내부 좌표를 사용합니다.
스크립트는 앱 선택과 해당 앱의 포커스가 필요하며, 터치 위치가 다른 창에 가려지면 중단합니다. 전체화면에서 실행하면 하단 로그에 앱 선택 안내를 표시합니다. 한 명령에 최대 256개 좌표를 쓸 수 있습니다.</p>
<p><b>mouse</b>는 각 좌표 명령 실행 시 읽은 실제 커서 위치입니다. 반복 중에도 갱신합니다.
한 번의 curve는 명령 시작 시 읽은 좌표로 경로를 유지합니다.
편집기와 메인 창 밖에서 우클릭하면 현재 입력 위치에 x,y가 추가됩니다.</p>
<p>시간은 0~600,000ms 정수입니다. 스크립트 전체 길이는 최대 100,000자입니다.
예제를 선택하면 기존 내용을 덮어씁니다. 저장 시 문법을 검사하며 오류는 잠시 로그로 표시합니다.</p>
<h2>명령어</h2>''' + commands + '''
<h2>누름 유지와 해제</h2>
<p>일반 press/mouse_press로 누른 입력은 정상 종료 시 해제됩니다.
switch/mouse_switch로 켠 입력은 정상 종료 뒤에도 유지될 수 있습니다.
해당 release 명령 또는 release_all로 해제하세요. 작동 중지·오류·대상 창 변경 등에서도 해제됩니다.</p>
<h2>지원 범위</h2><p>스크립트는 독립 터치로 실행됩니다. 실제 커서는 움직이지 않습니다.
버튼은 left만 지원합니다. 키보드 명령(key_press/key_release/key_switch), 문자 입력(text),
다른 마우스 버튼은 지원하지 않으며 저장·실행 시 오류로 안내합니다.</p>
<h2>예시</h2><pre>loop
curve_first_time 5
curve_last_time 5
curve 1200,800 mouse
sleep 5
loop_end</pre>
<p>1200,800을 실제 스킬 카드 좌표로 바꾸면 해당 카드에서 현재 커서 위치 mouse까지 반복 드래그합니다.</p>
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

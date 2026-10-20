"""On-demand Korean reference for the supported macro language."""
from html import escape
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton


COMMAND_HELP = [
    ('move X,Y', '가상 터치 위치를 옮긴다. 실제 커서는 움직이지 않는다.'),
    ('click X,Y [X,Y ...]', '각 좌표를 차례로 한 번씩 클릭한다. 클릭마다 설정된 클릭 유지 시간만큼 누른 뒤 뗀다.'),
    ('press X,Y', '지정 좌표를 누른다.'),
    ('release X,Y', '지정 좌표에서 누름을 해제한다.'),
    ('switch X,Y', '지정 좌표에서 누름과 해제를 전환한다.'),
    ('mouse_press left', '현재 가상 터치 위치를 누른다.'),
    ('mouse_release left', '현재 가상 터치를 해제한다.'),
    ('mouse_switch left', '현재 가상 터치의 누름과 해제를 전환한다.'),
    ('sleep 시간', '지정한 시간만큼 기다린다.'),
    ('curve X,Y X,Y [X,Y ...]', '첫 좌표를 누른 채 나머지 좌표로 드래그한다.'),
    ('curve_first_time 시간', '이후 드래그의 시작 지점 대기 시간을 정한다. 기본값은 0ms다.'),
    ('curve_last_time 시간', '이후 드래그의 끝 지점 대기 시간을 정한다. 기본값은 0ms다.'),
    ('curve_last_time untilcmd', '드래그가 끝나도 누름을 유지한다.'),
    ('curve_release', '드래그의 누름을 해제한다.'),
    ('release_all', '매크로가 유지 중인 모든 터치를 해제한다.'),
    ('loop [횟수]', '지정 횟수만큼 반복한다. 횟수를 생략하거나 loop until_release를 사용하면 단축키를 누르는 동안 반복한다.'),
    ('loop_end', '반복 구간을 끝낸다.'),
    ('release_actions', '이후 명령은 앞부분 실행이 끝나고 단축키를 놓은 뒤 실행한다.'),
]


def help_html():
    commands = ''.join(f'<tr><td><code>{escape(syntax)}</code></td><td>{escape(description)}</td></tr>'
                       for syntax, description in COMMAND_HELP)
    return """<h2>입력창 안내</h2>
<p>우클릭으로 현재 입력 위치에 좌표를 불러오기</p>
<h2>작성 방법</h2>
<ul>
<li>한 줄에 명령 하나를 작성한다.</li>
<li>대소문자는 구분하지 않는다. 빈 줄은 무시한다.</li>
<li><code>//</code> 뒤에 주석을 쓸 수 있다.</li>
<li>좌표는 대상 앱의 내부 영역을 기준으로 한다.</li>
<li>시간 단위는 ms다. <code>1000ms = 1초</code></li>
<li>예제를 선택하면 기존 내용을 덮어쓴다.</li>
<li>저장할 때 문법을 검사한다.</li>
</ul>
<h2>자동완성</h2>
<p>명령어를 입력하면 후보와 짧은 설명이 표시된다. ↑↓로 선택하고 Tab으로 입력한다.
Esc로 닫고, Ctrl+Space로 다시 연다. Enter는 줄바꿈이다.</p>
<p>자동완성은 줄 앞의 명령어에만 표시된다. 인수, 주석, 숫자는 자동완성하지 않는다.</p>
<h2>커서 좌표 사용</h2>
<p>좌표 대신 <code>mouse</code>를 쓰면 현재 커서 위치를 사용한다.</p>
<p>명령을 실행할 때마다 위치를 읽으므로 반복 중에도 갱신된다.</p>
<h2>명령어</h2>
<table width="100%" cellspacing="0" cellpadding="6">
<tr><th align="left">명령</th><th align="left">설명</th></tr>""" + commands + """</table>
<p><code>[ ]</code> 안의 항목은 생략할 수 있다. 실제 명령에는 대괄호를 쓰지 않는다.</p>
<p>클릭 유지 시간과 <code>curve</code> 구간당 이동 시간은 환경설정에서 조절한다.</p>
<h2>반복과 입력 해제</h2>
<ul>
<li><code>loop</code>와 <code>loop_end</code>는 짝으로 작성한다.</li>
<li><code>loop</code> 또는 <code>loop until_release</code>는 단축키를 놓으면 진행 중인 반복 회차를 마치고 멈춘다.</li>
<li>횟수를 지정한 <code>loop</code>는 단축키를 놓아도 지정 횟수만큼 실행한다.</li>
<li>정지하면 진행 중인 대기도 중단한다.</li>
<li><code>release_actions</code>는 반복문 밖에서 한 번만 쓴다. 이후 반복문에는 횟수가 필요하다.</li>
<li><code>press</code>로 누른 입력은 스크립트가 끝나면 해제된다.</li>
<li><code>switch</code>로 켠 입력은 종료 후에도 유지될 수 있다. <code>release</code>나 <code>release_all</code>로 해제한다.</li>
<li>정지·오류·대상 앱 변경 시에는 입력을 해제한다.</li>
</ul>
<h2>실행 조건과 제한</h2>
<p>대상 앱을 선택하고 해당 앱에 포커스를 둬야 한다. 터치 위치가 다른 창에 가려지면 중단한다.</p>
<p>스크립트는 실제 커서를 움직이지 않는 독립 터치로 실행한다. 버튼은 <code>left</code>만 지원한다.
키보드 입력, 문자 입력, 우클릭, 게임패드는 지원하지 않는다.</p>
<ul>
<li>좌표: 각 -99,999~99,999</li>
<li>시간: 0~600,000ms의 정수</li>
<li>명령당 좌표: 최대 256개</li>
<li>반복 횟수: 1~10,000회</li>
<li>반복문 중첩: 최대 8단계</li>
<li>스크립트 길이: 최대 100,000자</li>
</ul>"""


class ScriptHelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('스크립트 도움말')
        area = parent.screen().availableGeometry()
        self.resize(min(620, area.width()), min(600, area.height()))
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setObjectName('scriptHelp')
        browser.setHtml(help_html())
        layout.addWidget(browser)
        close = QPushButton('닫기')
        close.clicked.connect(self.accept)
        layout.addWidget(close)

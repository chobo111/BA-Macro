"""One process owns the lock; subsequent launches ask it to show its window."""
from hashlib import sha256
from pathlib import Path
import time

from PySide6.QtCore import QObject, QLockFile, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from native import allow_foreground, restore_process_window


class SingleInstance(QObject):
    activate_requested = Signal()

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'instance.lock'
        self.lock = QLockFile(str(path))
        self.lock.setStaleLockTime(0)
        digest = sha256(str(path).casefold().encode('utf-8')).hexdigest()[:24]
        self.name = 'ba-macro-' + digest
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._activate)
        self.owner = False

    def acquire(self):
        if self.lock.tryLock(0):
            self.owner = True
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                self.close()
                raise OSError('창 열기 요청을 받을 수 없습니다: ' + self.server.errorString())
            return True
        if self.lock.error() != QLockFile.LockError.LockFailedError:
            raise OSError('앱 실행 잠금 파일을 만들 수 없습니다.')
        pid, *_ = self.lock.getLockInfo()
        if pid > 0:
            allow_foreground(pid)
        # The first process may still be between acquiring its lock and listen().
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(150):
                socket.disconnectFromServer()
                return False
            socket.abort()
            time.sleep(.05)
        # Older running versions have the same lock, but no local server yet.
        if pid > 0 and restore_process_window(pid):
            return False
        raise OSError('실행 중인 앱의 창을 열지 못했습니다. 잠시 후 다시 실행하세요.')

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activate_requested.emit()

    def close(self):
        if self.owner:
            self.server.close()
            self.lock.unlock()
            self.owner = False

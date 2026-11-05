"""Separate POCO v3 entry point. The original app and v2 stay executable."""
import argparse
import faulthandler
import os
from pathlib import Path
import sys


def other_poco_processes():
    """Avoid concurrent UI instances issuing commands to the same motor bus."""
    app_dir = Path(__file__).resolve().parent
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = [s.decode(errors='replace') for s in (proc/'cmdline').read_bytes().split(b'\0') if s]
            if not args or 'python' not in Path(args[0]).name.lower():
                continue
            for value in args[1:]:
                if Path(value).name not in ('mainpyQt.py', 'mainpyQt_v2.py', 'mainpyQt_v3.py'):
                    continue
                path = Path(value)
                if not path.is_absolute():
                    path = (proc/'cwd').resolve() / path
                if path.resolve().parent == app_dir:
                    found.append(int(proc.name))
        except (OSError, PermissionError):
            continue
    return found


def main():
    parser = argparse.ArgumentParser(description='POCO UI v3')
    parser.add_argument('--windowed', action='store_true')
    parser.add_argument('--autostart', action='store_true',
                        help='Prepare the saved rest pose before any profile selection')
    args = parser.parse_args()
    faulthandler.enable(all_threads=True)
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from PyQt5.QtCore import QLockFile
    from ui_v3.theme import apply_theme
    app = QApplication(sys.argv[:1])
    apply_theme(app)
    # Share the v2 lock so the two modern UIs cannot command the motor bus together.
    lock = QLockFile(str(Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / ('poco-ui-v2-%d.lock' % os.getuid())))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if args.autostart:
            print('[POCO startup] An instance already holds the UI lock.', flush=True)
            return 2
        QMessageBox.information(None, 'POCO가 실행 중이에요', '이미 실행 중인 POCO 창을 사용해 주세요.')
        return 2
    peers = other_poco_processes()
    if peers:
        if args.autostart:
            print('[POCO startup] Another POCO app is running: %s' % peers, flush=True)
            return 2
        QMessageBox.information(None, 'POCO가 실행 중이에요', '기존 POCO 창을 정상 종료한 뒤 UI v3를 실행해 주세요.\n두 버전은 동시에 사용할 수 없어요.')
        return 2
    import mainpyQt as legacy
    from ui_v3.dialogs import PreparationDialogV3, ProfileDialogV3
    from ui_v3.worker import CameraWorkerV3
    # Change UI factories only in this process; original files and entry point are untouched.
    legacy.MonitorArmPreparationDialog = PreparationDialogV3
    legacy.UserProfileDialog = ProfileDialogV3
    legacy.CameraWorker = CameraWorkerV3
    from ui_v3.window import MainWindowV3
    print('[POCO startup] mode=%s' % ('boot: connect and rest' if args.autostart else 'manual'), flush=True)
    window = MainWindowV3(autostart=args.autostart)
    window.show() if args.windowed else window.showFullScreen()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())

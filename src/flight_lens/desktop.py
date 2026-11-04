"""Per-user Linux launchers and packaged icons; no GUI/network startup on setup."""
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP_ID = 'org.bullet.FlightLens'
APP_NAME = 'FlightLens'
WM_CLASS = 'FlightLens'
MODULE = 'flight_lens.app'
COMMENT = 'Offline MCAP flight overview and investigation'
TOOL_TIMEOUT_S = 5
ICON_SIZES = (256, 512)
MANIFEST_VERSION = 1


def _data_home():
    value = os.environ.get('XDG_DATA_HOME')
    # The XDG specification ignores relative paths.
    return Path(value) if value and Path(value).is_absolute() else Path.home() / '.local/share'


def _desktop_dir():
    command = shutil.which('xdg-user-dir')
    if command:
        try:
            result = subprocess.run([command, 'DESKTOP'], capture_output=True, text=True,
                                    timeout=TOOL_TIMEOUT_S, check=False)
            directory = Path(result.stdout.strip())
            if result.returncode == 0 and result.stdout.strip() and directory.is_absolute():
                return directory
        except (OSError, subprocess.TimeoutExpired):
            pass
    return Path.home() / 'Desktop'


def _quote_exec(value):
    if any(c in value for c in ('\n', '\r', '\0')) or '=' in value:
        raise ValueError('Unsupported character in launcher interpreter path')
    quoted = value.replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        quoted = quoted.replace(char, '\\' + char)
    # Desktop string escaping is decoded before Exec argument quoting.
    return '"' + quoted.replace('\\', '\\\\') + '"'


def _entry():
    # Resolving this symlink would discard the virtual environment.
    python = str(Path(sys.executable).absolute())
    # GIO checks the first executable before expanding literal percent escapes.
    # Put a percent-containing interpreter in env's argument position; no shell.
    prefix = '/usr/bin/env ' if '%' in python else ''
    if prefix and not Path('/usr/bin/env').is_file():
        raise ValueError('/usr/bin/env is required for a percent-containing interpreter path')
    return (f'[Desktop Entry]\nVersion=1.0\nType=Application\nName={APP_NAME}\n'
            f'Comment={COMMENT}\nExec={prefix}{_quote_exec(python)} -m {MODULE}\n'
            f'Icon={APP_ID}\nTerminal=false\nCategories=Science;\n'
            f'StartupWMClass={WM_CLASS}\nX-Bullet-Managed={APP_ID}\n').encode()


def _hash(data):
    return sha256(data).hexdigest()


def _icon_targets(data):
    return {data / f'icons/hicolor/{size}x{size}/apps/{APP_ID}.png': f'icon-{size}.png'
            for size in ICON_SIZES} | {data / f'icons/hicolor/scalable/apps/{APP_ID}.svg': 'icon.svg'}


def _manifest(data):
    path = data / APP_ID / 'launcher.json'
    if path.is_symlink():
        raise ValueError(f'Refusing symlink launcher manifest: {path}')
    if not path.exists():
        return path, {}, None
    try:
        value = json.loads(path.read_text())
        desktop = value['desktop']
        managed = value['files']
        allowed = {data / 'applications' / f'{APP_ID}.desktop', *_icon_targets(data)}
        if desktop is not None:
            desktop_path = Path(desktop)
            if not desktop_path.is_absolute() or desktop_path.name != f'{APP_ID}.desktop':
                raise ValueError('invalid managed desktop path')
            allowed.add(desktop_path)
        if (value['version'] != MANIFEST_VERSION or value['app_id'] != APP_ID
                or not isinstance(managed, dict) or any(Path(p) not in allowed for p in managed)
                or any(not isinstance(digest, str) or len(digest) != 64 for digest in managed.values())):
            raise ValueError('invalid launcher ownership data')
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f'Invalid launcher manifest {path}: {error}') from error
    return path, {Path(p): digest for p, digest in managed.items()}, desktop


def _check_owned(path, managed):
    if path.is_symlink():
        raise FileExistsError(f'Refusing symlink launcher file: {path}')
    if path.exists() and (not path.is_file() or managed.get(path) != _hash(path.read_bytes())):
        raise FileExistsError(f'Refusing to change unmanaged or modified file: {path}')


def _write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _refresh(desktop):
    notes = []
    commands = (
        ('update-desktop-database', [str(_data_home() / 'applications')]),
        ('gtk-update-icon-cache', ['--force', '--ignore-theme-index', str(_data_home() / 'icons/hicolor')]),
    )
    for name, args in commands:
        command = shutil.which(name)
        if command:
            try:
                subprocess.run([command, *args], capture_output=True,
                               timeout=TOOL_TIMEOUT_S, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Registration remains valid without optional cache helpers.
    if desktop is not None:
        gio = shutil.which('gio')
        try:
            if gio:
                result = subprocess.run([gio, 'set', str(desktop), 'metadata::trusted', 'true'],
                                        capture_output=True, timeout=TOOL_TIMEOUT_S, check=False)
                if result.returncode == 0:
                    return notes
        except (OSError, subprocess.TimeoutExpired):
            pass
        notes.append('Your desktop may require right-click → Allow Launching for the shortcut.')
    return notes


def install_launcher(*, menu_only=False):
    if not sys.platform.startswith('linux'):
        raise ValueError('Desktop launcher setup currently supports Linux only')
    data = _data_home()
    manifest, managed, _ = _manifest(data)
    menu = data / 'applications' / f'{APP_ID}.desktop'
    desktop = None if menu_only else _desktop_dir() / f'{APP_ID}.desktop'
    assets = files(__package__).joinpath('assets')
    targets = {path: assets.joinpath(name).read_bytes() for path, name in _icon_targets(data).items()}
    targets[menu] = _entry()
    if desktop is not None:
        targets[desktop] = targets[menu]
    # Preflight the complete change before writing/removing any owned file.
    for path in set(managed) | set(targets):
        _check_owned(path, managed)
    backups = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
               for path in set(managed) | set(targets) | {manifest}}
    try:
        for path, content in targets.items():
            _write(path, content, 0o755 if path == desktop else 0o644)
        for path in set(managed) - set(targets):
            path.unlink(missing_ok=True)
        content = dict(version=MANIFEST_VERSION, app_id=APP_ID,
                       desktop=None if desktop is None else str(desktop),
                       files={str(path): _hash(value) for path, value in targets.items()})
        _write(manifest, (json.dumps(content, indent=2) + '\n').encode(), 0o600)
    except OSError as error:
        # Attempt every rollback; do not rewrite files that were never changed.
        failures = []
        for path, backup in backups.items():
            try:
                if backup is None:
                    path.unlink(missing_ok=True)
                elif (not path.exists() or path.read_bytes() != backup[0]
                      or path.stat().st_mode & 0o777 != backup[1]):
                    _write(path, *backup)
            except OSError as rollback_error:
                failures.append(f'{path}: {rollback_error}')
        if failures:
            raise OSError(f'{error}; rollback incomplete: {"; ".join(failures)}') from error
        raise
    return [f'Installed {APP_NAME}: {menu}', *([] if desktop is None else [f'Desktop shortcut: {desktop}']),
            *_refresh(desktop)]


def uninstall_launcher():
    if not sys.platform.startswith('linux'):
        raise ValueError('Desktop launcher setup currently supports Linux only')
    manifest, managed, _ = _manifest(_data_home())
    for path in managed:
        _check_owned(path, managed)
    for path in managed:
        path.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    _refresh(None)
    return [f'Removed managed {APP_NAME} launchers and icons.']


def add_launcher_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--install-launcher', action='store_true', help='Install user menu and desktop launchers')
    group.add_argument('--uninstall-launcher', action='store_true', help='Remove managed user launchers and icons')
    parser.add_argument('--menu-only', action='store_true', help='With --install-launcher, omit/remove the desktop shortcut')


def handle_launcher_arguments(args):
    if args.menu_only and not args.install_launcher:
        raise ValueError('--menu-only requires --install-launcher')
    if not args.install_launcher and not args.uninstall_launcher:
        return None
    messages = install_launcher(menu_only=args.menu_only) if args.install_launcher else uninstall_launcher()
    print('\n'.join(messages))
    return 0


def configure_application(application):
    from PySide6 import QtGui
    icon = QtGui.QIcon()
    assets = files(__package__).joinpath('assets')
    for size in ICON_SIZES:
        pixmap = QtGui.QPixmap()
        if not pixmap.loadFromData(assets.joinpath(f'icon-{size}.png').read_bytes(), 'PNG'):
            raise ValueError('Packaged application icon could not be loaded')
        icon.addPixmap(pixmap)
    application.setApplicationName(WM_CLASS)
    application.setDesktopFileName(APP_ID)
    application.setWindowIcon(icon)

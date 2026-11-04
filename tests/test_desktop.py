"""Linux integration uses only isolated user directories and fake launch targets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import flight_lens.desktop as desktop


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    home=tmp_path/'home space'
    home.mkdir()
    data=tmp_path/'data space'
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.setenv('XDG_DATA_HOME',str(data))
    monkeypatch.setenv('XDG_CONFIG_HOME',str(home/'.config'))
    monkeypatch.setattr(desktop.shutil,'which',lambda name:None)
    return home,data


def test_install_update_menu_only_and_remove(isolated,monkeypatch):
    home,data=isolated
    # Absolute path and symlink are retained, rather than resolving out of a venv.
    interpreter=home/'venv space/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    monkeypatch.setattr(desktop.sys,'executable',str(interpreter))
    result=desktop.install_launcher()
    menu=data/'applications'/f'{desktop.APP_ID}.desktop'
    shortcut=home/'Desktop'/menu.name
    assert menu.exists() and shortcut.exists()
    text=menu.read_text()
    assert f'Exec="{interpreter}" -m {desktop.MODULE}' in text
    assert 'Terminal=false' in text and f'Icon={desktop.APP_ID}' in text
    assert shortcut.stat().st_mode & 0o111
    assert any('Allow Launching' in note for note in result)
    for size in (256,512):assert (data/f'icons/hicolor/{size}x{size}/apps/{desktop.APP_ID}.png').exists()
    assert (data/f'icons/hicolor/scalable/apps/{desktop.APP_ID}.svg').exists()
    assert desktop.install_launcher()
    desktop.install_launcher(menu_only=True)
    assert menu.exists() and not shortcut.exists()
    desktop.uninstall_launcher();desktop.uninstall_launcher()
    assert not menu.exists() and not (data/desktop.APP_ID/'launcher.json').exists()
    assert not list(data.rglob('*.png'))


def test_unmanaged_menu_and_icon_conflicts_write_nothing(isolated):
    _,data=isolated
    menu=data/'applications'/f'{desktop.APP_ID}.desktop'
    menu.parent.mkdir(parents=True)
    menu.write_text('unrelated')
    with pytest.raises(FileExistsError,match='unmanaged'):desktop.install_launcher()
    assert menu.read_text()=='unrelated' and not (data/'icons').exists()
    menu.unlink()
    icon=data/f'icons/hicolor/256x256/apps/{desktop.APP_ID}.png'
    icon.parent.mkdir(parents=True);icon.write_bytes(b'unrelated')
    with pytest.raises(FileExistsError):desktop.install_launcher()
    assert icon.read_bytes()==b'unrelated' and not menu.exists()


def test_modified_file_is_preserved_on_reinstall_and_uninstall(isolated):
    _,data=isolated
    desktop.install_launcher(menu_only=True)
    menu=data/'applications'/f'{desktop.APP_ID}.desktop'
    menu.write_text(menu.read_text()+'# user edit\n')
    before=menu.read_bytes()
    with pytest.raises(FileExistsError):desktop.install_launcher()
    with pytest.raises(FileExistsError):desktop.uninstall_launcher()
    assert menu.read_bytes()==before


def test_bad_manifest_and_symlink_are_preserved(isolated):
    home,data=isolated
    manifest=data/desktop.APP_ID/'launcher.json'
    manifest.parent.mkdir(parents=True);manifest.write_text('{}')
    with pytest.raises(ValueError,match='manifest'):desktop.install_launcher()
    manifest.unlink()
    menu=data/'applications'/f'{desktop.APP_ID}.desktop'
    menu.parent.mkdir(parents=True)
    unrelated=home/'unrelated';unrelated.write_text('keep')
    menu.symlink_to(unrelated)
    with pytest.raises(FileExistsError,match='symlink'):desktop.install_launcher()
    assert unrelated.read_text()=='keep'


def test_localized_desktop_and_optional_tool_failures(isolated,monkeypatch):
    home,data=isolated
    localized=home/'Localized Desktop'
    monkeypatch.setattr(desktop.shutil,'which',lambda name:'/tools/'+name)
    def run(command,**kwargs):
        if command[0].endswith('xdg-user-dir'):return SimpleNamespace(returncode=0,stdout=str(localized)+'\n')
        raise subprocess.TimeoutExpired(command,5)
    monkeypatch.setattr(desktop.subprocess,'run',run)
    notes=desktop.install_launcher()
    assert (localized/f'{desktop.APP_ID}.desktop').exists()
    assert any('Allow Launching' in note for note in notes)
    desktop.uninstall_launcher()
    assert not (localized/f'{desktop.APP_ID}.desktop').exists()


def test_relative_xdg_and_failed_discovery_use_home_defaults(isolated,monkeypatch):
    home,_=isolated
    monkeypatch.setenv('XDG_DATA_HOME','relative')
    monkeypatch.setattr(desktop.shutil,'which',lambda _: '/missing-tool')
    monkeypatch.setattr(desktop.subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(OSError('missing')))
    desktop.install_launcher()
    assert (home/'.local/share/applications'/f'{desktop.APP_ID}.desktop').exists()
    assert (home/'Desktop'/f'{desktop.APP_ID}.desktop').exists()


def test_failed_write_rolls_back_registration(isolated,monkeypatch):
    _,data=isolated
    real_write=desktop._write
    def fail_manifest(path,content,mode):
        if path.name=='launcher.json':raise OSError('disk full')
        real_write(path,content,mode)
    monkeypatch.setattr(desktop,'_write',fail_manifest)
    with pytest.raises(OSError,match='disk full'):desktop.install_launcher()
    assert not list(data.rglob('*.desktop')) and not list(data.rglob('*.png'))


def test_setup_cli_is_headless_and_never_starts_gui(isolated,monkeypatch,capsys):
    import importlib
    entry=importlib.import_module(desktop.MODULE)
    def no_gui(*args,**kwargs):raise AssertionError('GUI startup during launcher setup')
    monkeypatch.setattr(entry.QtWidgets,'QApplication',no_gui)
    assert entry.main(['--install-launcher','--menu-only'])==0
    assert 'Installed' in capsys.readouterr().out
    assert entry.main(['--uninstall-launcher'])==0
    with pytest.raises(SystemExit) as error:entry.main(['--menu-only'])
    assert error.value.code==2
    with pytest.raises(SystemExit):entry.main(['--install-launcher','--uninstall-launcher'])


def test_packaged_icons_have_expected_sizes_and_application_identity():
    # Keep Qt resource ownership entirely inside one short-lived GUI process.
    script = """
from PySide6 import QtGui,QtWidgets
from flight_lens import desktop
app=QtWidgets.QApplication([])
desktop.configure_application(app)
assert app.desktopFileName()==desktop.APP_ID and app.applicationName()==desktop.WM_CLASS
assert not app.windowIcon().isNull()
for size in (256,512):
    data=desktop.files(desktop.__package__).joinpath('assets',f'icon-{size}.png').read_bytes()
    image=QtGui.QImage.fromData(data)
    assert image.width()==size and image.height()==size
assert not app.windowIcon().pixmap(64,64).isNull()
"""
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_desktop_entry_validates_and_exec_roundtrips_reserved_path(isolated,monkeypatch):
    # Real GIO parses the desktop quoting and spawns a fake interpreter, never the GUI.
    home,data=isolated
    # Fixture masks optional commands; discover real paths without changing setup behavior.
    gio='/usr/bin/gio' if Path('/usr/bin/gio').exists() else None
    validate='/usr/bin/desktop-file-validate' if Path('/usr/bin/desktop-file-validate').exists() else None
    if not gio or not validate:pytest.skip('optional desktop validator/GIO unavailable')
    output=home/'arguments.json'
    interpreter=home/'env $cash %percent "quote" `tick` \\slash/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('#!'+sys.executable+'\nimport json,sys\nfrom pathlib import Path\nPath('+repr(str(output))+').write_text(json.dumps(sys.argv[1:]))\n')
    interpreter.chmod(0o755)
    monkeypatch.setattr(desktop.sys,'executable',str(interpreter))
    desktop.install_launcher(menu_only=True)
    menu=data/'applications'/f'{desktop.APP_ID}.desktop'
    result=subprocess.run([validate,str(menu)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    result=subprocess.run([gio,'launch',str(menu)],cwd='/',capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    deadline=time.monotonic()+3
    while not output.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert json.loads(output.read_text())==['-m',desktop.MODULE]


@pytest.mark.parametrize('bad_path',['/tmp/env=bad/python','/tmp/env\nline/python','/tmp/env\rline/python','/tmp/env\0bad/python'])
def test_unsupported_exec_path_is_rejected_before_writes(isolated,monkeypatch,bad_path):
    _,data=isolated
    monkeypatch.setattr(desktop.sys,'executable',bad_path)
    with pytest.raises(ValueError,match='Unsupported character'):desktop.install_launcher()
    assert not list(data.rglob('*.desktop')) and not list(data.rglob('*.png'))


def test_failed_update_restores_previous_owned_files(isolated,monkeypatch):
    _,data=isolated
    desktop.install_launcher(menu_only=True)
    before={str(path):path.read_bytes() for path in data.rglob('*') if path.is_file()}
    monkeypatch.setattr(desktop.sys,'executable','/other/environment/python')
    real_write=desktop._write
    def fail_manifest(path,content,mode):
        if path.name=='launcher.json':raise OSError('manifest write failed')
        real_write(path,content,mode)
    monkeypatch.setattr(desktop,'_write',fail_manifest)
    with pytest.raises(OSError,match='manifest write failed'):desktop.install_launcher(menu_only=True)
    after={str(path):path.read_bytes() for path in data.rglob('*') if path.is_file()}
    assert before==after
    desktop.uninstall_launcher()

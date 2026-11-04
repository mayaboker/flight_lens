# Packaged Linux GUI icons and launchers

- Status: Implemented; verified 2026-11-02
- Implementation commit: Pending

Bundle original navy/mint/blue flight-themed SVG and 256/512 PNG icons in Bullet
View and FlightLens wheels; configure window/application icons and stable desktop
identities. Add --install-launcher (menu + desktop by default), --menu-only and
--uninstall-launcher, handled before GUI/network startup. Use per-user XDG paths,
xdg-user-dir desktop discovery, absolute current Python without dereferencing its
venv symlink, correctly quoted module launch commands and managed-file ownership.
Repeat installs must be safe; do not overwrite/remove unrelated or modified files.
Desktop executable/trust setup is best effort where supported. No new dependency,
shared package, system-wide installation, file association, Windows bundle or
saved local model/connection settings. Preserve pending wheel task and other work.

Test isolated XDG/home/desktop paths, spaces/reserved characters, missing optional
utilities, conflicts and repeat/remove/menu-only behavior; build/install wheels
outside the checkout, inspect resources and desktop entries and visually inspect
icons. No real user shortcuts, cameras or motors are actuated during verification.
No commits or pushes are requested.

## Implementation result

Both GUI packages contain original flight-themed SVG and 256/512 PNG resources.
Qt application/window icons and DesktopFileName/WM_CLASS identities match
org.bullet.BulletView and org.bullet.FlightLens. App setup flags run before GUI,
config/model loading and network workers. Per-user XDG menu/desktop entries use
the current absolute Python path without resolving its virtual-environment
symlink. A private ownership/hash manifest protects unmanaged or modified files;
registration preflights all changes, updates atomically per file and attempts
rollback on write failure. Menu-only removes a previous unchanged owned desktop
shortcut; uninstall removes only owned files and is repeatable. Optional desktop/
icon cache and trusted-shortcut helpers are bounded/best-effort. GIO's literal
percent executable edge case is handled using /usr/bin/env as a shell-free prefix
only for such interpreter paths. README usage and a workspace icon regeneration
script are provided. No runtime dependency or project entry-point name changed.

## Verification result

185 tests passed: bullet_view 78 (offscreen), flight_lens 107 (xcb). The 15 launcher
tests in each project cover repeat/menu-only/remove, localized/fallback desktop,
XDG paths, spaces and reserved characters, bad/modified/symlink ownership data,
initial/update filesystem rollback, optional utility failures, headless CLI
handling and icon resources/identity. desktop-file-validate and real GIO launching
of a fake interpreter confirm argument roundtrips without starting flight IO.
An earlier combined Qt check passed assertions but failed at binding shutdown;
icon-resource checks were isolated in their own GUI process and the final full
suites exited successfully.

Both GUI wheels were rebuilt in workspace deploy/. ZIP/asset checks passed and
the local FlightLens config file is excluded. Installed both wheels plus shared
messages in /tmp/bullet-gui-launcher-check-3pu8dxih/venv, constraining runtime
versions from the shared lockfile exports. The first offline dependency resolution
was missing cached wheels; locked dependencies were then obtained normally.
From /tmp with PYTHONPATH/DISPLAY unset and isolated HOME/XDG directories, both
installed setup commands passed menu/desktop/install/update/menu-only/remove,
including a localized desktop path. GIO launched the real installed module with
--help; installed Qt icons loaded without checkout paths. Real invisible X11
windows in separate processes confirmed the expected WM_CLASS and window icons.
Both icons were visually inspected. uv lock --check and whitespace checks passed.

The host xdg-user-dir script does not handle a space-containing XDG_CONFIG_HOME;
that external command returns its HOME/Desktop fallback in that situation.
The installed check used a normal config directory and verified spaces in HOME,
XDG_DATA_HOME, the localized desktop and interpreter launch tests. No real user
menu/desktop registration, camera/flight actuation, commits or pushes occurred.
Pending wheel-task work and the user-owned docs/record.md were preserved.
Implementation commit remains Pending.

# KDE Plasma integration (Plasma / KWin 6.5 or newer, Wayland)

The regular layer-shell `bottom` layer is **above** Plasma's desktop, hiding
icons. Its default pointer input region also consumes desktop clicks. On KDE,
linux-wallpaperengine now uses the background layer and an empty input region.
A small, transparent Plasma wallpaper and a KWin stacking constraint keep the
renderer below the actual desktop. No pixels are copied through the CPU, and
no native code is injected into plasmashell.

## One-time setup

From `build/output/kde`:

```sh
python3 install.py --list
python3 install.py --desktop DESKTOP_ID  # replace with a numeric ID from --list
systemctl --user restart plasma-plasmashell.service
```

The one-time restart is needed because an already-created opaque Qt desktop
cannot acquire a working alpha buffer just by changing its clear color.
Only the selected desktop changes. Repeat installation for additional screens
before restarting Plasma. Do not select an inactive/disconnected containment.
The integration remains selected across login sessions. Start the renderer as
usual, e.g. `linux-wallpaperengine --screen-root DP-1 --bg PATH` or through a GUI.
No extra renderer arguments or recurring Plasma restarts are needed.

Icons, widgets, right-click menus, dragging, selection and panels remain owned
by Plasma. Direct mouse interaction/parallax in the external wallpaper is
unavailable in this mode: Wayland does not broadcast the desktop's pointer
input to background clients. Other desktop environments and window previews
keep their existing behavior. `LWE_KDE_DESKTOP=0` opts out of the KDE defaults.

KWin watches renderer windows and shows the saved static fallback on the
matching screen when the renderer stops, including after crashes/SIGKILL.
The previous wallpaper plugin and its original settings are retained. Solid
colors and image paths are used for the fallback; other wallpaper plugins use
a black fallback. A directory-based image wallpaper may need an explicit image
file as `FallbackImage`. Stop the renderer before changing wallpaper type.

To return to the previous wallpaper type:

```sh
python3 install.py --desktop DESKTOP_ID --restore
```

The KWin script can be disabled in System Settings → Window Management → KWin
Scripts. It only manages `linux-wallpaperengine` surfaces and never hides,
minimizes or disables Plasma's desktop. `--layer` is deliberately overridden
in KDE desktop mode to prevent accidental overlays.

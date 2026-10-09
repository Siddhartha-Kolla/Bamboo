"""System-tray icon for the quick-add popup: Quick add / Restart / Start with Windows / Quit.

Runs pystray on its own thread. Menu clicks are never handled here; they are put on the popup's
event queue as strings and the Tk thread acts on them, because Tk must only be touched from its own thread.
"""


def _icon_image():
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 8, 60, 58), radius=9, fill=(31, 33, 41, 255), outline=(124, 156, 255, 255), width=3)
    d.rectangle((4, 8, 60, 22), fill=(124, 156, 255, 255))
    for x in (18, 32, 46):
        d.rounded_rectangle((x - 3, 3, x + 3, 15), radius=2, fill=(236, 238, 244, 255))
    for row in range(3):
        for col in range(4):
            x, y = 12 + col * 11, 30 + row * 9
            d.rectangle((x, y, x + 6, y + 5), fill=(236, 238, 244, 255) if (row, col) != (1, 2) else (240, 179, 90, 255))
    return img


def start(events, hotkey_label, autostart_enabled, autostart_toggle):
    """Show the tray icon. Returns the icon (call .stop() to remove it), or None if pystray is missing."""
    try:
        import pystray
    except ImportError:
        return None

    def put(name):
        return lambda icon, item: events.put(name)

    menu = pystray.Menu(
        pystray.MenuItem("Quick add  (%s)" % hotkey_label, put("tray-add"), default=True),
        pystray.MenuItem("Settings…", put("tray-settings")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart  (load code changes)", put("tray-restart")),
        pystray.MenuItem("Start with Windows", lambda icon, item: autostart_toggle(),
                         checked=lambda item: autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", put("tray-quit")),
    )
    icon = pystray.Icon("quickadd", _icon_image(), "Quick add calendar", menu)
    icon.run_detached()
    return icon

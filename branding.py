"""Bamboo's pictures at runtime: the panda for headers, the tray and window icons.

The PNGs in assets/png are made once by tools/make_assets.py and committed, so this module only loads
them. Every function returns None (or does nothing) when a file is missing, and the caller keeps its
plain fallback, so a damaged install still starts.
"""
import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
PNG = ASSETS / "png"
ICO = ASSETS / "bamboo.ico"


def sizes(kind):
    """The pixel sizes available for 'tile', 'face' or 'icon', ascending."""
    found = [int(m.group(1)) for f in PNG.glob("%s-*.png" % kind) if (m := re.fullmatch(r"%s-(\d+)\.png" % kind, f.name))]
    return sorted(found)


def nearest(kind, px):
    """The available size closest to `px` (preferring one that is not smaller), or None."""
    options = sizes(kind)
    if not options:
        return None
    bigger = [s for s in options if s >= px]
    return min(bigger) if bigger and min(bigger) <= px * 1.4 else min(options, key=lambda s: abs(s - px))


def path(kind, px):
    size = nearest(kind, px)
    return PNG / ("%s-%d.png" % (kind, size)) if size else None


def photo(kind, px, master=None):
    """A Tk PhotoImage of the panda near `px` pixels, or None. The caller must keep a reference to it."""
    import tkinter as tk
    file = path(kind, px)
    if file is None or not file.exists():
        return None
    try:
        return tk.PhotoImage(master=master, file=str(file))
    except tk.TclError:
        return None


def pil(kind, px):
    """A Pillow image resized to exactly `px` x `px` (for the tray icon), or None."""
    file = path(kind, px)
    if file is None or not file.exists():
        return None
    try:
        from PIL import Image
        return Image.open(file).convert("RGBA").resize((px, px), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - a broken image must never stop the app
        return None


def set_window_icon(window, px=64):
    """Give a Tk window the panda as its icon. Returns the image (keep it alive) or None."""
    image = photo("icon", px, master=window)
    if image is not None:
        try:
            window.iconphoto(False, image)
            window._bamboo_icon = image          # Tk drops the picture if nothing references it
        except Exception:  # noqa: BLE001 - cosmetic only
            return None
    return image

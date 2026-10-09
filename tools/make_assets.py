"""Build Bamboo's icons from assets/bamboo-logo.svg.   Developer tool; users never run it.

    pip install -r requirements-dev.txt
    python tools/make_assets.py [--preview preview.png]

From the one logo it derives three SVGs and renders them to PNG at the sizes the app uses:

    assets/bamboo-tile.svg   the panda holding a calendar on a rounded navy square   (headers, 512 px consent logo)
    assets/bamboo-face.svg   the head only, on transparent, with lighter ears         (tray, readable at 16-32 px)
    assets/bamboo-icon.svg   the face on a rounded navy square                        (bamboo.ico, shortcuts)

The generated files are committed, so running the app needs no extra dependency.
"""
import io
import re
import struct
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"
PNG = ASSETS / "png"
NAVY = "#202947"
TILE_BG, TILE_HALO = "#2e3b68", "#3d4c82"
SIZES = {"tile": (48, 64, 96, 128, 256, 512), "face": (16, 20, 24, 32, 40, 48, 64), "icon": (16, 24, 32, 48, 64, 128, 256)}
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def sections(logo):
    """Split the logo's drawing into its named parts, using the comments the artwork already has."""
    lines = logo.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if "clip-path=\"url(#canvas)\"" in l)
    parts, name = {"background": []}, "background"
    for line in lines[start + 1:]:
        found = re.match(r"\s*<!--\s*(.*?)\s*-->", line)
        if found:
            name = found.group(1)
            parts[name] = []
        elif line.strip() in ("</g>", "</svg>") and name == "paws in front of calendar" and len(parts[name]) >= 2:
            continue                                   # the closing tags of the whole drawing
        else:
            parts[name].append(line)
    return parts


def face_elements(parts):
    """Head only: ears, face, eye patches, cheeks and muzzle. Ears get lighter so they show on a dark taskbar."""
    ears = "\n".join(parts["ears"]).replace("#1b2444", "#34407c").replace("#414e82", "#6a78b8")
    face = [l for l in parts["body and face"] if "#f5f2e9" in l]
    return "\n".join([ears] + face + parts["eye patches"] + parts["cheeks and muzzle"])


def build_svgs(logo):
    parts = sections(logo)
    # The tile sits on dark navy windows, so it gets a lighter background than the logo's own; otherwise the
    # panda's dark ears and body would disappear into the window.
    background = "\n".join(parts["background"]).replace("#2c385f", TILE_HALO).replace('fill="%s"' % NAVY, 'fill="%s"' % TILE_BG, 1)
    everything = background + "\n" + "\n".join(l for key in parts if key != "background" for l in parts[key])
    tile = ('<svg xmlns="http://www.w3.org/2000/svg" width="380" height="380" viewBox="40 82 380 380">\n'
            '  <defs><clipPath id="tile"><rect x="40" y="82" width="380" height="380" rx="76"/></clipPath></defs>\n'
            '  <g clip-path="url(#tile)">\n%s\n  </g>\n</svg>\n' % everything)
    face = face_elements(parts)
    face_svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="284" height="284" viewBox="90 92 284 284">\n%s\n</svg>\n' % face)
    icon = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">\n'
            '  <rect width="512" height="512" rx="112" fill="%s"/>\n'
            '  <circle cx="256" cy="256" r="196" fill="#2c385f"/>\n'
            '  <g transform="translate(56 56) scale(1.408) translate(-90 -92)">\n%s\n  </g>\n</svg>\n' % (NAVY, face))
    return {"bamboo-tile.svg": tile, "bamboo-face.svg": face_svg, "bamboo-icon.svg": icon}


def render(svg_text, size):
    import resvg_py
    data = resvg_py.svg_to_bytes(svg_string=svg_text, width=size, height=size)
    return bytes(data)


def write_ico(path, blobs):
    """A Windows .ico made of PNG images (supported since Vista), one per size."""
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, body = b"", b""
    for size, blob in blobs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(body))
        body += blob
    path.write_bytes(header + entries + body)


def preview(out, rendered):
    """A contact sheet: the face and icon at tray sizes, enlarged, on a dark and a light taskbar."""
    from PIL import Image
    sheet = Image.new("RGBA", (860, 380), (0, 0, 0, 0))
    for row, (bg, y) in enumerate((((24, 24, 28, 255), 0), ((240, 240, 244, 255), 190))):
        sheet.paste(Image.new("RGBA", (860, 190), bg), (0, y))
        x = 16
        for name in ("face", "icon"):
            for size in (16, 24, 32):
                im = Image.open(io.BytesIO(rendered[(name, size)])).convert("RGBA")
                sheet.alpha_composite(im, (x, y + 20))
                big = im.resize((size * 5, size * 5), Image.NEAREST)
                sheet.alpha_composite(big, (x, y + 20 + 40) if size == 16 else (x, y + 20 + 40))
                x += size * 5 + 12
    sheet.convert("RGB").save(out)


def main(argv):
    logo = ASSETS / "bamboo-logo.svg"
    PNG.mkdir(parents=True, exist_ok=True)
    svgs = build_svgs(logo)
    rendered = {}
    for name, text in svgs.items():
        (ASSETS / name).write_text(text, encoding="utf-8")
        kind = name.split("-")[1].split(".")[0]
        for size in SIZES[kind]:
            blob = render(text, size)
            rendered[(kind, size)] = blob
            (PNG / ("%s-%d.png" % (kind, size))).write_bytes(blob)
    write_ico(ASSETS / "bamboo.ico", [(s, rendered[("icon", s)]) for s in ICO_SIZES])
    print("wrote %d PNGs, 3 SVGs and bamboo.ico to %s" % (len(rendered), ASSETS))
    if "--preview" in argv:
        preview(argv[argv.index("--preview") + 1], rendered)


if __name__ == "__main__":
    main(sys.argv[1:])

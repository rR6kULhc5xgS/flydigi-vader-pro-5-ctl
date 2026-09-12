#!/usr/bin/env python3
"""Pull the controller artwork out of a local Flydigi Space Station install.

The product photo the GUI uses is Flydigi's own artwork, so it is not
redistributed with this project. If you have Space Station installed (or its
installer extracted), this copies the image for your controller out of the
app's asar archive into flydigi/gui/assets/.

Without it the GUI falls back to a drawn schematic, which works fine -- the
photo is only nicer to look at.

    python3 tools/extract-assets.py /path/to/Flydigi Space Station/resources/app.asar
"""

import json
import os
import struct
import sys

# device type -> file we care about, relative to the asar root
WANTED = {
    130: "/.vite/build/assets/images/product/Controller/f5/130/main.png",
    144: "/.vite/build/assets/images/product/Controller/f5/144/main.png",
    145: "/.vite/build/assets/images/product/Controller/f5/145/main.png",
}
DEST_NAME = "vader5pro.png"


def read_asar_index(fh):
    head = fh.read(16)
    if len(head) < 16:
        raise SystemExit("not an asar archive")
    header_size = struct.unpack_from("<I", head, 4)[0]
    json_len = struct.unpack_from("<I", head, 12)[0]
    fh.seek(16)
    index = json.loads(fh.read(json_len).decode("utf-8"))
    return index, 8 + header_size


def walk(node, path=""):
    if "files" in node:
        for name, child in node["files"].items():
            yield from walk(child, path + "/" + name)
    else:
        yield path, node


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    asar = sys.argv[1]
    device_type = int(sys.argv[2]) if len(sys.argv) > 2 else 130
    wanted = WANTED.get(device_type)
    if wanted is None:
        print(f"no artwork known for device type {device_type}; "
              f"known: {sorted(WANTED)}")
        return 2

    dest_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "flydigi", "gui", "assets")
    os.makedirs(dest_dir, exist_ok=True)

    with open(asar, "rb") as fh:
        index, base = read_asar_index(fh)
        for path, node in walk(index):
            if path != wanted:
                continue
            fh.seek(base + int(node["offset"]))
            data = fh.read(node["size"])
            dest = os.path.join(dest_dir, DEST_NAME)
            with open(dest, "wb") as out:
                out.write(data)
            print(f"wrote {dest} ({len(data)} bytes)")
            return 0
    print(f"{wanted} not found in {asar}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

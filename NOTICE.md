# Notice

This project is an independent, unofficial reimplementation. It is not
affiliated with, endorsed by, or supported by Flydigi.

It was produced by examining the Windows "Flydigi Space Station" software in
order to interoperate with hardware the author owns — the controller's
configuration protocol is otherwise undocumented and has no Linux support.

## What this repository does and does not contain

Everything here is original work: the protocol documentation in `docs/` is
written from analysis, and the code is an independent implementation. The
`.proto` files under `docs/proto/` are recovered interface descriptions and
contain no vendor code.

Not included, and not redistributable:

- Flydigi's decompiled application code.
- Flydigi's binaries, installers, or bundled libraries.
- Flydigi's artwork. The GUI can use the product photo for your controller if
  you extract it from your own installation with `tools/extract-assets.py`;
  otherwise it draws a schematic.

Flydigi, Vader, and Space Station are trademarks of their respective owner
and are used here only to say what this software is compatible with.

## Warranty

None. This writes to your controller's flash. It takes backups and verifies
what it writes, but you use it at your own risk. Firmware flashing is
deliberately not implemented, since that is the operation that could render a
controller unusable.

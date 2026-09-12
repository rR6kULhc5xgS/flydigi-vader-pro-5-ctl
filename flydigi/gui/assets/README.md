# Artwork

The GUI will use Flydigi's product photo of the controller if it is present
here as `vader5pro.png`. That image is Flydigi's, so it is not distributed
with this project.

To use it, extract it from your own Space Station installation:

    python3 tools/extract-assets.py "/path/to/Flydigi Space Station/resources/app.asar"

Without it the controller view falls back to a drawn schematic. Everything
works either way — the photo is only nicer to look at.

"""devp's mascot, a bunny magician, drawn in the terminal with half-block characters."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

# The mascot's pixel art (assets/mascot.png), one character per pixel. Each terminal
# cell shows two stacked pixels, so these 30 rows draw as 15 lines, 30 columns wide.
_PIXELS = (
    "         ..........           ",
    " ...     .hHhhhhhh.     ...   ",
    ".###.    .hHhhhhhh.    .###.  ",
    " .#p#.   .hHhhhhhh.   .#p#.   ",
    "  .#p#.  .hHhhhhhh.  .#p#.    ",
    "   .#p#. .rrrrrrrr. .#p#.     ",
    "    .#p#..rrrrrrrr..#p#.      ",
    "    ..hhhhhhhhhhhhhhhh..      ",
    "    ....................   r  ",
    "    .ssssssssssssssssss.  rrr ",
    "   .####################.  r  ",
    "   .####.###########.###.     ",
    "   .###..##########..###.   r ",
    "   .###..####nn####..###.  rrr",
    "   .#pp####.#..#.####pp#. rrrr",
    "   .########.##.######s#.  rrr",
    "    .################s#.   r r",
    "     .##############s#.    .. ",
    "       ..............     ..  ",
    "       .############.... ..   ",
    "      .##############.##..    ",
    "     .#..###########s.###.    ",
    "     ..##.##########s....     ",
    "     .#..###########s#.       ",
    "     .##############s#.       ",
    "      .#############s.        ",
    "      .#############s.        ",
    "     .######....######.       ",
    "      ......    ......        ",
    "                              ",
)

_PALETTE = {
    ".": "#201e1d",  # outline
    "#": "#ffffff",  # fur
    "s": "#eae7e7",  # fur shading
    "p": "#ffe0d9",  # inner ears and cheeks
    "n": "#ff9783",  # nose
    "h": "#444141",  # hat
    "H": "#7d7979",  # hat highlight
    "r": "#ec3013",  # hat band and wand sparks
}

MASCOT_WIDTH = len(_PIXELS[0])
MASCOT_HEIGHT = len(_PIXELS) // 2


def render_mascot() -> Text:
    """The mascot as Rich text: '▀' with the top pixel as foreground and the bottom as
    background (or '▄' when only the bottom pixel is set), transparent elsewhere."""
    text = Text(no_wrap=True, overflow="crop")
    for top_row, bottom_row in zip(_PIXELS[0::2], _PIXELS[1::2]):
        for top, bottom in zip(top_row, bottom_row):
            top_color, bottom_color = _PALETTE.get(top), _PALETTE.get(bottom)
            if top_color and bottom_color:
                text.append("▀", Style(color=top_color, bgcolor=bottom_color))
            elif top_color:
                text.append("▀", Style(color=top_color))
            elif bottom_color:
                text.append("▄", Style(color=bottom_color))
            else:
                text.append(" ")
        text.append("\n")
    text.rstrip()
    return text

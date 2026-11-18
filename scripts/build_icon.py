"""Regenerate the deterministic multi-resolution Windows application icon."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_icon(output: Path) -> None:
    scale = 4
    canvas = Image.new("RGBA", (512 * scale, 512 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def points(values: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return [(x * scale, y * scale) for x, y in values]

    draw.rounded_rectangle(
        (0, 0, 512 * scale - 1, 512 * scale - 1),
        radius=104 * scale,
        fill="#173d32",
    )
    draw.polygon(
        points(
            [
                (88, 156),
                (130, 144),
                (178, 146),
                (220, 164),
                (256, 184),
                (256, 394),
                (210, 370),
                (158, 360),
                (88, 372),
            ]
        ),
        fill="#f5eddf",
    )
    draw.polygon(
        points(
            [
                (424, 156),
                (382, 144),
                (334, 146),
                (292, 164),
                (256, 184),
                (256, 394),
                (302, 370),
                (354, 360),
                (424, 372),
            ]
        ),
        fill="#f5eddf",
    )
    draw.line(points([(256, 184), (256, 394)]), fill="#d8cdb9", width=15 * scale)
    for left, right in (
        ((152, 222), (234, 237)),
        ((152, 276), (234, 291)),
        ((360, 222), (278, 237)),
        ((360, 276), (278, 291)),
    ):
        draw.line(points([left, right]), fill="#9eaa9f", width=13 * scale)
    draw.polygon(
        points([(326, 154), (350, 150), (376, 151), (376, 262), (351, 243), (326, 262)]),
        fill="#7b2d3a",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="ICO", sizes=[(size, size) for size in SIZES], bitmap_format="bmp")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "packaging/windows/paper-reader.ico",
    )
    args = parser.parse_args()
    build_icon(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

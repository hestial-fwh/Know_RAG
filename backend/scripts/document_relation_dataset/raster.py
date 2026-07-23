from __future__ import annotations

from dataclasses import dataclass
import random
import struct
import zlib
from pathlib import Path


@dataclass
class Raster:
    width: int
    height: int
    pixels: bytearray

    @classmethod
    def white(cls, width: int, height: int) -> "Raster":
        return cls(width, height, bytearray([255] * width * height * 3))

    def point(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        offset = (y * self.width + x) * 3
        self.pixels[offset : offset + 3] = bytes(color)

    def line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
        thickness: int = 1,
    ) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            radius = max(0, thickness // 2)
            for yy in range(y0 - radius, y0 + radius + 1):
                for xx in range(x0 - radius, x0 + radius + 1):
                    self.point(xx, yy, color)
            if x0 == x1 and y0 == y1:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def rect(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
        *,
        fill: bool = False,
        thickness: int = 2,
    ) -> None:
        if fill:
            for y in range(max(0, y0), min(self.height, y1)):
                for x in range(max(0, x0), min(self.width, x1)):
                    self.point(x, y, color)
            return
        for offset in range(thickness):
            self.line(x0, y0 + offset, x1, y0 + offset, color)
            self.line(x0, y1 - offset, x1, y1 - offset, color)
            self.line(x0 + offset, y0, x0 + offset, y1, color)
            self.line(x1 - offset, y0, x1 - offset, y1, color)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, raster: Raster) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = bytearray()
    stride = raster.width * 3
    for row in range(raster.height):
        rows.append(0)
        start = row * stride
        rows.extend(raster.pixels[start : start + stride])
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", raster.width, raster.height, 8, 2, 0, 0, 0),
    )
    payload += _chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
    payload += _chunk(b"IEND", b"")
    path.write_bytes(payload)


def make_asset(kind: str, seed: int, *, width: int = 320, height: int = 220) -> Raster:
    rng = random.Random(seed)
    raster = Raster.white(width, height)
    ink = (25, 35, 45)
    accent = (45, 105, 170)
    pale = (225, 235, 245)

    if kind == "paper":
        raster.line(35, height - 30, 35, 25, ink, 2)
        raster.line(35, height - 30, width - 20, height - 30, ink, 2)
        points = []
        for index in range(9):
            x = 35 + index * (width - 70) // 8
            y = height - 45 - rng.randrange(15, height - 75)
            points.append((x, y))
        for first, second in zip(points, points[1:]):
            raster.line(*first, *second, accent, 4)
        for x, y in points:
            raster.rect(x - 3, y - 3, x + 3, y + 3, accent, fill=True)
    elif kind == "product_manual":
        raster.rect(35, 35, width - 35, height - 35, pale, fill=True)
        raster.rect(35, 35, width - 35, height - 35, ink, thickness=3)
        for index in range(3):
            x0 = 55 + index * 78
            raster.rect(x0, 70, x0 + 55, 145, accent, thickness=3)
            if index < 2:
                raster.line(x0 + 57, 107, x0 + 75, 107, ink, 3)
                raster.line(x0 + 70, 102, x0 + 75, 107, ink, 3)
                raster.line(x0 + 70, 112, x0 + 75, 107, ink, 3)
    elif kind == "calligraphy":
        for _ in range(7):
            x0 = rng.randrange(45, width - 45)
            y0 = rng.randrange(25, height - 25)
            x1 = max(20, min(width - 20, x0 + rng.randrange(-80, 81)))
            y1 = max(20, min(height - 20, y0 + rng.randrange(-90, 91)))
            raster.line(x0, y0, x1, y1, ink, rng.randrange(7, 15))
        raster.rect(10, 10, width - 10, height - 10, (180, 170, 150), thickness=2)
    else:  # album
        colors = [(190, 215, 235), (230, 205, 185), (195, 225, 195)]
        for index, color in enumerate(colors):
            x0 = 15 + index * (width - 30) // 3
            x1 = 15 + (index + 1) * (width - 30) // 3
            raster.rect(x0, 15, x1, height - 15, color, fill=True)
        for _ in range(16):
            x = rng.randrange(20, width - 20)
            y = rng.randrange(20, height - 20)
            raster.line(x - 8, y, x + 8, y, ink, 2)
            raster.line(x, y - 8, x, y + 8, ink, 2)
    return raster


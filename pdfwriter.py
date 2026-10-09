"""
Минимальный генератор PDF без внешних зависимостей.

Поддерживает то, что нужно отчёту: встроенный TrueType-шрифт с кириллицей
(CIDFontType2 + Identity-H), текст, линии, заливки и автоматическую разбивку
на страницы. Карта ToUnicode добавляется, поэтому текст в готовом файле
выделяется и ищется.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Iterable, Sequence

A4_WIDTH = 595.28
A4_HEIGHT = 841.89

FONT_SEARCH_PATHS = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/freefont/FreeSans.ttf",
     "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
)


class FontNotFoundError(RuntimeError):
    """В системе не нашлось TrueType-шрифта с поддержкой кириллицы."""


# ---------------------------------------------------------------------------
# Разбор TrueType
# ---------------------------------------------------------------------------


class TrueTypeFont:
    """Читает из TTF ровно то, что требуется для встраивания в PDF."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.tables = self._read_table_directory()

        head = self._table("head")
        self.units_per_em = struct.unpack(">H", head[18:20])[0] or 1000
        x_min, y_min, x_max, y_max = struct.unpack(">hhhh", head[36:44])
        self.bbox = tuple(
            round(value * 1000 / self.units_per_em)
            for value in (x_min, y_min, x_max, y_max)
        )

        hhea = self._table("hhea")
        self.ascent = round(struct.unpack(">h", hhea[4:6])[0] * 1000 / self.units_per_em)
        self.descent = round(struct.unpack(">h", hhea[6:8])[0] * 1000 / self.units_per_em)
        self._num_h_metrics = struct.unpack(">H", hhea[34:36])[0]

        try:
            os2 = self._table("OS/2")
            self.cap_height = round(
                struct.unpack(">h", os2[88:90])[0] * 1000 / self.units_per_em
            ) if len(os2) >= 90 else self.ascent
        except KeyError:
            self.cap_height = self.ascent

        try:
            post = self._table("post")
            self.italic_angle = struct.unpack(">i", post[4:8])[0] / 65536.0
        except KeyError:
            self.italic_angle = 0.0

        self._cmap = self._read_cmap()
        self._advances = self._read_advances()
        self.name = self.path.stem.replace(" ", "")

    # -- таблицы -----------------------------------------------------------

    def _read_table_directory(self) -> dict[str, tuple[int, int]]:
        count = struct.unpack(">H", self.data[4:6])[0]
        tables: dict[str, tuple[int, int]] = {}
        for index in range(count):
            entry = 12 + index * 16
            tag = self.data[entry:entry + 4].decode("latin-1")
            offset, length = struct.unpack(">II", self.data[entry + 8:entry + 16])
            tables[tag] = (offset, length)
        return tables

    def _table(self, tag: str) -> bytes:
        if tag not in self.tables:
            raise KeyError(tag)
        offset, length = self.tables[tag]
        return self.data[offset:offset + length]

    def _read_cmap(self) -> dict[int, int]:
        cmap = self._table("cmap")
        count = struct.unpack(">H", cmap[2:4])[0]

        chosen: int | None = None
        fallback: int | None = None
        for index in range(count):
            entry = 4 + index * 8
            platform, encoding, offset = struct.unpack(">HHI", cmap[entry:entry + 8])
            if (platform, encoding) in ((3, 10), (0, 4), (0, 6)):
                chosen = offset
            elif (platform, encoding) in ((3, 1), (0, 3)) and chosen is None:
                chosen = offset
            elif fallback is None:
                fallback = offset

        offset = chosen if chosen is not None else fallback
        if offset is None:
            return {}

        table_format = struct.unpack(">H", cmap[offset:offset + 2])[0]
        if table_format == 4:
            return self._read_cmap_format4(cmap, offset)
        if table_format == 12:
            return self._read_cmap_format12(cmap, offset)
        return {}

    @staticmethod
    def _read_cmap_format4(cmap: bytes, offset: int) -> dict[int, int]:
        seg_count = struct.unpack(">H", cmap[offset + 6:offset + 8])[0] // 2
        ends_at = offset + 14
        starts_at = ends_at + seg_count * 2 + 2
        deltas_at = starts_at + seg_count * 2
        ranges_at = deltas_at + seg_count * 2

        mapping: dict[int, int] = {}
        for segment in range(seg_count):
            end = struct.unpack(">H", cmap[ends_at + segment * 2:ends_at + segment * 2 + 2])[0]
            start = struct.unpack(">H", cmap[starts_at + segment * 2:starts_at + segment * 2 + 2])[0]
            delta = struct.unpack(">h", cmap[deltas_at + segment * 2:deltas_at + segment * 2 + 2])[0]
            range_offset_at = ranges_at + segment * 2
            range_offset = struct.unpack(">H", cmap[range_offset_at:range_offset_at + 2])[0]

            if start > end or end == 0xFFFF and start == 0xFFFF:
                continue

            for code in range(start, end + 1):
                if range_offset == 0:
                    glyph = (code + delta) & 0xFFFF
                else:
                    glyph_at = range_offset_at + range_offset + (code - start) * 2
                    if glyph_at + 2 > len(cmap):
                        continue
                    glyph = struct.unpack(">H", cmap[glyph_at:glyph_at + 2])[0]
                    if glyph:
                        glyph = (glyph + delta) & 0xFFFF
                if glyph:
                    mapping[code] = glyph
        return mapping

    @staticmethod
    def _read_cmap_format12(cmap: bytes, offset: int) -> dict[int, int]:
        group_count = struct.unpack(">I", cmap[offset + 12:offset + 16])[0]
        mapping: dict[int, int] = {}
        for index in range(group_count):
            entry = offset + 16 + index * 12
            start, end, glyph = struct.unpack(">III", cmap[entry:entry + 12])
            if end - start > 0x10000:
                continue
            for code in range(start, end + 1):
                mapping[code] = glyph + (code - start)
        return mapping

    def _read_advances(self) -> list[int]:
        hmtx = self._table("hmtx")
        advances: list[int] = []
        for index in range(self._num_h_metrics):
            at = index * 4
            if at + 2 > len(hmtx):
                break
            advances.append(struct.unpack(">H", hmtx[at:at + 2])[0])
        return advances or [self.units_per_em]

    # -- метрики -----------------------------------------------------------

    def glyph_id(self, char: str) -> int:
        return self._cmap.get(ord(char), 0)

    def advance(self, glyph: int) -> float:
        if glyph < len(self._advances):
            units = self._advances[glyph]
        else:
            units = self._advances[-1]
        return units * 1000.0 / self.units_per_em

    def width(self, text: str, size: float) -> float:
        total = sum(self.advance(self.glyph_id(char)) for char in text)
        return total * size / 1000.0

    def encode(self, text: str) -> tuple[str, dict[int, str]]:
        """Возвращает hex-строку из идентификаторов глифов и карту для ToUnicode."""
        glyphs: list[str] = []
        used: dict[int, str] = {}
        for char in text:
            glyph = self.glyph_id(char)
            glyphs.append(f"{glyph:04X}")
            used[glyph] = char
        return "".join(glyphs), used


def find_system_fonts() -> tuple[TrueTypeFont, TrueTypeFont]:
    """Ищет обычное и жирное начертание среди системных шрифтов."""
    for regular_path, bold_path in FONT_SEARCH_PATHS:
        if Path(regular_path).exists():
            regular = TrueTypeFont(regular_path)
            bold = TrueTypeFont(bold_path) if Path(bold_path).exists() else regular
            return regular, bold
    raise FontNotFoundError(
        "Не найден системный TrueType-шрифт с кириллицей "
        "(искали DejaVu Sans, Liberation Sans, FreeSans)"
    )


# ---------------------------------------------------------------------------
# Документ
# ---------------------------------------------------------------------------


class PdfDocument:
    """
    Страничный документ с координатами, отсчитываемыми сверху.

    Рисование идёт в текущую страницу; при нехватке места вызывается
    ensure_space(), которая заводит новую.
    """

    def __init__(
        self,
        fonts: dict[str, TrueTypeFont],
        page_width: float = A4_WIDTH,
        page_height: float = A4_HEIGHT,
        margin: float = 42.0,
    ) -> None:
        self.fonts = fonts
        self.page_width = page_width
        self.page_height = page_height
        self.margin = margin
        self.title = ""

        self._pages: list[list[str]] = []
        self._used_glyphs: dict[str, dict[int, str]] = {key: {} for key in fonts}
        self._on_new_page = None
        self.y = margin
        self.new_page()

    # -- страницы ----------------------------------------------------------

    @property
    def content_width(self) -> float:
        return self.page_width - 2 * self.margin

    def new_page(self) -> None:
        self._pages.append([])
        self.y = self.margin
        if self._on_new_page is not None:
            self._on_new_page(self)

    def set_page_header(self, callback) -> None:
        """Колбэк, рисующий шапку на каждой новой странице."""
        self._on_new_page = callback
        callback(self)

    def ensure_space(self, height: float) -> None:
        if self.y + height > self.page_height - self.margin:
            self.new_page()

    def space(self, height: float) -> None:
        self.y += height

    # -- примитивы ---------------------------------------------------------

    def _emit(self, command: str) -> None:
        self._pages[-1].append(command)

    def _flip(self, y: float) -> float:
        return self.page_height - y

    def rect(self, x: float, y: float, width: float, height: float,
             color: str, radius: float = 0.0) -> None:
        if width <= 0 or height <= 0:
            return
        red, green, blue = _rgb(color)
        bottom = self._flip(y + height)
        if radius <= 0:
            self._emit(
                f"{red:.3f} {green:.3f} {blue:.3f} rg "
                f"{x:.2f} {bottom:.2f} {width:.2f} {height:.2f} re f"
            )
            return

        radius = min(radius, width / 2, height / 2)
        top = bottom + height
        right = x + width
        k = radius * 0.5523
        self._emit(
            f"{red:.3f} {green:.3f} {blue:.3f} rg "
            f"{x + radius:.2f} {bottom:.2f} m "
            f"{right - radius:.2f} {bottom:.2f} l "
            f"{right - radius + k:.2f} {bottom:.2f} {right:.2f} {bottom + radius - k:.2f} "
            f"{right:.2f} {bottom + radius:.2f} c "
            f"{right:.2f} {top - radius:.2f} l "
            f"{right:.2f} {top - radius + k:.2f} {right - radius + k:.2f} {top:.2f} "
            f"{right - radius:.2f} {top:.2f} c "
            f"{x + radius:.2f} {top:.2f} l "
            f"{x + radius - k:.2f} {top:.2f} {x:.2f} {top - radius + k:.2f} "
            f"{x:.2f} {top - radius:.2f} c "
            f"{x:.2f} {bottom + radius:.2f} l "
            f"{x:.2f} {bottom + radius - k:.2f} {x + radius - k:.2f} {bottom:.2f} "
            f"{x + radius:.2f} {bottom:.2f} c f"
        )

    def line(self, x1: float, y1: float, x2: float, y2: float,
             color: str, width: float = 0.6) -> None:
        red, green, blue = _rgb(color)
        self._emit(
            f"{red:.3f} {green:.3f} {blue:.3f} RG {width:.2f} w "
            f"{x1:.2f} {self._flip(y1):.2f} m {x2:.2f} {self._flip(y2):.2f} l S"
        )

    def polyline(self, points: Sequence[tuple[float, float]],
                 color: str, width: float = 1.4) -> None:
        if len(points) < 2:
            return
        red, green, blue = _rgb(color)
        head = f"{points[0][0]:.2f} {self._flip(points[0][1]):.2f} m"
        tail = " ".join(
            f"{x:.2f} {self._flip(y):.2f} l" for x, y in points[1:]
        )
        self._emit(
            f"{red:.3f} {green:.3f} {blue:.3f} RG {width:.2f} w "
            "1 J 1 j " + head + " " + tail + " S"
        )

    def circle(self, x: float, y: float, radius: float, color: str) -> None:
        red, green, blue = _rgb(color)
        cy = self._flip(y)
        k = radius * 0.5523
        self._emit(
            f"{red:.3f} {green:.3f} {blue:.3f} rg "
            f"{x - radius:.2f} {cy:.2f} m "
            f"{x - radius:.2f} {cy + k:.2f} {x - k:.2f} {cy + radius:.2f} "
            f"{x:.2f} {cy + radius:.2f} c "
            f"{x + k:.2f} {cy + radius:.2f} {x + radius:.2f} {cy + k:.2f} "
            f"{x + radius:.2f} {cy:.2f} c "
            f"{x + radius:.2f} {cy - k:.2f} {x + k:.2f} {cy - radius:.2f} "
            f"{x:.2f} {cy - radius:.2f} c "
            f"{x - k:.2f} {cy - radius:.2f} {x - radius:.2f} {cy - k:.2f} "
            f"{x - radius:.2f} {cy:.2f} c f"
        )

    def text(self, x: float, y: float, value: str, font: str = "regular",
             size: float = 9.0, color: str = "#000000",
             align: str = "left") -> None:
        """Рисует строку; y — базовая линия, отсчитанная сверху страницы."""
        if not value:
            return

        type_face = self.fonts[font]
        if align == "right":
            x -= type_face.width(value, size)
        elif align == "center":
            x -= type_face.width(value, size) / 2

        encoded, used = type_face.encode(value)
        self._used_glyphs[font].update(used)
        red, green, blue = _rgb(color)
        self._emit(
            f"BT {red:.3f} {green:.3f} {blue:.3f} rg /{font[0].upper()}{_font_index(self.fonts, font)} "
            f"{size:.2f} Tf 1 0 0 1 {x:.2f} {self._flip(y):.2f} Tm <{encoded}> Tj ET"
        )

    # -- текстовые утилиты -------------------------------------------------

    def width_of(self, value: str, font: str = "regular", size: float = 9.0) -> float:
        return self.fonts[font].width(value, size)

    def ellipsize(self, value: str, font: str, size: float, max_width: float) -> str:
        type_face = self.fonts[font]
        if type_face.width(value, size) <= max_width:
            return value
        trimmed = value
        while trimmed and type_face.width(trimmed + "…", size) > max_width:
            trimmed = trimmed[:-1]
        return (trimmed + "…") if trimmed else ""

    def wrap(self, value: str, font: str, size: float,
             max_width: float, max_lines: int = 2) -> list[str]:
        type_face = self.fonts[font]
        words = value.split()
        lines: list[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip()
            if type_face.width(candidate, size) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break

        if len(lines) < max_lines and current:
            lines.append(current)
        if not lines:
            return [""]

        if len(lines) == max_lines:
            remaining = value[len(" ".join(lines)):].strip()
            if remaining:
                lines[-1] = self.ellipsize(lines[-1] + " " + remaining, font, size, max_width)
        return lines

    # -- сборка файла ------------------------------------------------------

    def render(self) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        font_objects: dict[str, int] = {}
        for name, type_face in self.fonts.items():
            font_objects[name] = self._build_font(add, name, type_face)

        page_ids: list[int] = []
        content_ids: list[int] = []
        for commands in self._pages:
            stream = "\n".join(commands).encode("latin-1", errors="replace")
            content_ids.append(add(_stream_object({}, stream)))
            page_ids.append(0)

        pages_id = len(objects) + len(self._pages) + 1
        resources = " ".join(
            f"/{name[0].upper()}{_font_index(self.fonts, name)} {ref} 0 R"
            for name, ref in font_objects.items()
        )

        for index, content_id in enumerate(content_ids):
            page_ids[index] = add(
                (
                    f"<< /Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {self.page_width:.2f} {self.page_height:.2f}] "
                    f"/Resources << /Font << {resources} >> >> "
                    f"/Contents {content_id} 0 R >>"
                ).encode("latin-1")
            )

        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        pages_ref = add(
            (
                f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>"
            ).encode("latin-1")
        )
        catalog_id = add(f"<< /Type /Catalog /Pages {pages_ref} 0 R >>".encode("latin-1"))
        info_id = add(
            b"<< /Producer (Smart Feedback Processor) /Title "
            + _pdf_text(self.title)
            + b" >>"
        )

        return _assemble(objects, catalog_id, info_id)

    def _build_font(self, add, name: str, type_face: TrueTypeFont) -> int:
        used = self._used_glyphs[name]
        font_file_id = add(
            _stream_object(
                {"Length1": len(type_face.data)},
                type_face.data,
            )
        )
        descriptor_id = add(
            (
                f"<< /Type /FontDescriptor /FontName /{type_face.name} /Flags 4 "
                f"/FontBBox [{type_face.bbox[0]} {type_face.bbox[1]} "
                f"{type_face.bbox[2]} {type_face.bbox[3]}] "
                f"/ItalicAngle {type_face.italic_angle:.0f} /Ascent {type_face.ascent} "
                f"/Descent {type_face.descent} /CapHeight {type_face.cap_height} "
                f"/StemV 80 /FontFile2 {font_file_id} 0 R >>"
            ).encode("latin-1")
        )

        widths = " ".join(
            f"{glyph} [{type_face.advance(glyph):.0f}]"
            for glyph in sorted(used)
        )
        descendant_id = add(
            (
                f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{type_face.name} "
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
                "/Supplement 0 >> "
                f"/FontDescriptor {descriptor_id} 0 R /DW 1000 /W [{widths}] "
                "/CIDToGIDMap /Identity >>"
            ).encode("latin-1")
        )
        to_unicode_id = add(_stream_object({}, _to_unicode_cmap(used)))

        return add(
            (
                f"<< /Type /Font /Subtype /Type0 /BaseFont /{type_face.name} "
                f"/Encoding /Identity-H /DescendantFonts [{descendant_id} 0 R] "
                f"/ToUnicode {to_unicode_id} 0 R >>"
            ).encode("latin-1")
        )


# ---------------------------------------------------------------------------
# Низкоуровневая сборка PDF
# ---------------------------------------------------------------------------


def _font_index(fonts: dict[str, TrueTypeFont], name: str) -> int:
    return list(fonts).index(name) + 1


def _stream_object(extra: dict[str, object], payload: bytes) -> bytes:
    compressed = zlib.compress(payload, 9)
    entries = "".join(f" /{key} {value}" for key, value in extra.items())
    header = (
        f"<< /Length {len(compressed)} /Filter /FlateDecode{entries} >>\nstream\n"
    ).encode("latin-1")
    return header + compressed + b"\nendstream"


def _to_unicode_cmap(used: dict[int, str]) -> bytes:
    entries = sorted(used.items())
    chunks: list[str] = []
    for start in range(0, len(entries), 100):
        block = entries[start:start + 100]
        lines = "\n".join(
            f"<{glyph:04X}> <{''.join(f'{ord(part):04X}' for part in char)}>"
            for glyph, char in block
        )
        chunks.append(f"{len(block)} beginbfchar\n{lines}\nendbfchar")

    body = "\n".join(chunks)
    return (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{body}\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
    ).encode("latin-1")


def _pdf_text(value: str) -> bytes:
    payload = value.encode("utf-16-be")
    escaped = b"".join(
        b"\\" + bytes([byte]) if byte in (0x28, 0x29, 0x5C) else bytes([byte])
        for byte in payload
    )
    return b"(\xfe\xff" + escaped + b")"


def _assemble(objects: list[bytes], catalog_id: int, info_id: int) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []

    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1")
        out += body
        out += b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")

    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R "
        f"/Info {info_id} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return (
        int(value[0:2], 16) / 255.0,
        int(value[2:4], 16) / 255.0,
        int(value[4:6], 16) / 255.0,
    )

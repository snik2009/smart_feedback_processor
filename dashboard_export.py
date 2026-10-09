"""
Выгрузка аналитического дашборда в CSV и PDF.

Обе выгрузки строятся из одного набора секций (build_report_sections), поэтому
цифры в файлах и на странице не расходятся. PDF дополнительно содержит графики
и собирается модулем pdfwriter без внешних зависимостей.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from analytics import DashboardData, MAX_RATING, MOOD_FALLBACK
from dashboard import (
    AXIS,
    GRID,
    INK,
    MUTED,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    SERIES_1,
    format_percent,
    format_rating,
    label_indexes,
    nice_scale,
    tick_text,
)
from pdfwriter import PdfDocument, find_system_fonts

CSV_DELIMITER = ";"

PAGE_BACKGROUND = "#ffffff"
PANEL_BORDER = "#e1e6eb"
INK_SECONDARY = "#52606d"

TITLE = "Аналитический дашборд"
SUBTITLE = "Smart Feedback Processor"


@dataclass
class ReportSection:
    """Одна таблица отчёта: одинаково ложится и в CSV, и в PDF."""

    title: str
    headers: tuple[str, ...]
    rows: list[tuple[str, ...]]
    widths: tuple[float, ...]
    aligns: tuple[str, ...]


# ---------------------------------------------------------------------------
# Общие данные отчёта
# ---------------------------------------------------------------------------


def build_report_sections(data: DashboardData) -> list[ReportSection]:
    totals = data.totals
    sections: list[ReportSection] = [
        ReportSection(
            title="Сводная статистика",
            headers=("Показатель", "Значение"),
            rows=[
                ("Отзывов проанализировано", str(totals.feedbacks)),
                ("Средняя оценка отзыва", format_rating(totals.avg_rating)),
                ("Отзывов с оценкой", str(totals.rated_feedbacks)),
                ("Оценок критериев качества", str(totals.quality_mentions)),
                ("Проблемных оценок (1–3 балла)", str(totals.problem_mentions)),
                ("Доля проблемных оценок", format_percent(totals.problem_share)),
                ("Позитивных упоминаний", str(totals.positives)),
                ("Негативных упоминаний", str(totals.negatives)),
                ("Вопросов от клиентов", str(totals.questions)),
                ("Задач в работу", str(totals.tasks)),
                ("Запросов от клиентов", str(totals.client_requests)),
                ("Эмоциональных отзывов", _share_text(totals.emotional, totals.feedbacks)),
                ("Отзывов в грубой форме", _share_text(totals.rude, totals.feedbacks)),
                ("Отзывов в неформальном стиле", _share_text(totals.informal, totals.feedbacks)),
            ],
            widths=(0.62, 0.38),
            aligns=("left", "right"),
        )
    ]

    if data.moods:
        sections.append(
            ReportSection(
                title="Настроение отзывов",
                headers=("Настроение", "Отзывов", "Доля"),
                rows=[
                    (bucket.name, str(bucket.amount), format_percent(bucket.share))
                    for bucket in data.moods
                ],
                widths=(0.62, 0.19, 0.19),
                aligns=("left", "right", "right"),
            )
        )

    if data.timeline:
        sections.append(
            ReportSection(
                title="Динамика показателей за период",
                headers=(
                    "Период", "Отзывов", "Средняя оценка",
                    "Позитивных", "Негативных", "Проблемных оценок",
                ),
                rows=[
                    (
                        point.label,
                        str(point.feedbacks),
                        format_rating(point.avg_rating),
                        str(point.positives),
                        str(point.negatives),
                        str(point.problem_mentions),
                    )
                    for point in data.timeline
                ],
                widths=(0.22, 0.13, 0.19, 0.15, 0.15, 0.16),
                aligns=("left", "right", "right", "right", "right", "right"),
            )
        )

    if data.bottlenecks:
        sections.append(
            ReportSection(
                title="Узкие места",
                headers=(
                    "Критерий качества", "Подразделение", "Упоминаний",
                    "Проблемных", "Доля проблем", "Средняя оценка", "Задач",
                ),
                rows=[
                    (
                        item.name,
                        item.responsible,
                        str(item.mentions),
                        str(item.problems),
                        format_percent(item.problem_share),
                        format_rating(item.avg_rating),
                        str(item.tasks),
                    )
                    for item in data.bottlenecks
                ],
                widths=(0.28, 0.23, 0.12, 0.12, 0.09, 0.09, 0.07),
                aligns=("left", "left", "right", "right", "right", "right", "right"),
            )
        )

    if data.tasks:
        sections.append(
            ReportSection(
                title="Задачи, поставленные по отзывам",
                headers=("Задача", "Подразделение", "Количество"),
                rows=[
                    (item.name, item.responsible, str(item.amount))
                    for item in data.tasks
                ],
                widths=(0.42, 0.40, 0.18),
                aligns=("left", "left", "right"),
            )
        )

    if data.divisions:
        sections.append(
            ReportSection(
                title="Эффективность подразделений",
                headers=(
                    "Подразделение", "Руководитель", "Отзывов закреплено",
                    "Оценок", "Проблемных", "Доля проблем", "Средняя оценка", "Задач",
                ),
                rows=[
                    (
                        item.name,
                        item.fio or "—",
                        str(item.assigned_feedbacks),
                        str(item.mentions),
                        str(item.problems),
                        format_percent(item.problem_share),
                        format_rating(item.avg_rating),
                        str(item.tasks),
                    )
                    for item in data.divisions
                    if item.mentions or item.assigned_feedbacks
                ],
                widths=(0.23, 0.19, 0.11, 0.09, 0.12, 0.09, 0.10, 0.07),
                aligns=(
                    "left", "left", "right", "right", "right", "right", "right", "right",
                ),
            )
        )

    return sections


def _share_text(amount: int, total: int) -> str:
    if not total:
        return "0"
    return f"{amount} ({format_percent(amount / total)})"


def _meta_rows(data: DashboardData) -> list[tuple[str, str]]:
    rows = [
        ("Период", data.period.label()),
        ("Сформирован", datetime.now().strftime("%d.%m.%Y %H:%M")),
    ]
    if data.unprocessed:
        rows.append(("Не обработано отзывов", str(data.unprocessed)))
    if data.undated:
        rows.append(("Без распознанной даты", str(data.undated)))
    return rows


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def build_dashboard_csv(data: DashboardData) -> bytes:
    """Все секции дашборда в одном CSV, разделённые пустой строкой."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=CSV_DELIMITER, lineterminator="\r\n")

    writer.writerow([TITLE])
    for name, value in _meta_rows(data):
        writer.writerow([name, value])

    for section in build_report_sections(data):
        writer.writerow([])
        writer.writerow([section.title])
        writer.writerow(list(section.headers))
        for row in section.rows:
            writer.writerow(list(row))

    # BOM — чтобы Excel открыл файл в UTF-8.
    return buffer.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

BODY_SIZE = 8.0
HEAD_SIZE = 7.0
ROW_HEIGHT = 13.0
SECTION_GAP = 16.0


def build_dashboard_pdf(data: DashboardData) -> bytes:
    """Отчёт с ключевыми показателями, графиками и всеми таблицами."""
    regular, bold = find_system_fonts()
    doc = PdfDocument({"regular": regular, "bold": bold})
    doc.title = f"{TITLE} — {data.period.label()}"

    doc.set_page_header(_page_header)
    _draw_title(doc, data)

    if data.has_data:
        _draw_kpi(doc, data)
        _draw_mood(doc, data)
        _draw_timeline_charts(doc, data)
        _draw_bar_section(doc, data)

    for section in build_report_sections(data):
        _draw_table(doc, section)

    if not data.has_data:
        doc.ensure_space(30)
        doc.text(doc.margin, doc.y + 10, "За выбранный период нет обработанных отзывов.",
                 "regular", 10, INK_SECONDARY)
        doc.space(24)

    return doc.render()


def _page_header(doc: PdfDocument) -> None:
    doc.text(doc.margin, doc.margin - 14, SUBTITLE, "regular", 7.5, MUTED)
    doc.line(doc.margin, doc.margin - 8,
             doc.page_width - doc.margin, doc.margin - 8, GRID, 0.5)
    doc.y = doc.margin + 6


def _draw_title(doc: PdfDocument, data: DashboardData) -> None:
    doc.text(doc.margin, doc.y + 12, TITLE, "bold", 17, INK)
    doc.space(26)
    for name, value in _meta_rows(data):
        doc.text(doc.margin, doc.y + 8, f"{name}: {value}", "regular", 8.5, INK_SECONDARY)
        doc.space(12)
    doc.space(8)


def _draw_kpi(doc: PdfDocument, data: DashboardData) -> None:
    totals = data.totals
    tiles = (
        (str(totals.feedbacks), "Отзывов"),
        (format_rating(totals.avg_rating), "Средняя оценка"),
        (str(totals.quality_mentions), "Оценок критериев"),
        (format_percent(totals.problem_share), "Доля проблемных"),
        (str(totals.tasks), "Задач в работу"),
    )

    height = 44.0
    doc.ensure_space(height + 12)
    gap = 8.0
    width = (doc.content_width - gap * (len(tiles) - 1)) / len(tiles)
    top = doc.y

    for index, (value, caption) in enumerate(tiles):
        x = doc.margin + index * (width + gap)
        doc.rect(x, top, width, height, "#f7f9fb", radius=4)
        doc.text(x + 9, top + 22, value, "bold", 15, INK)
        doc.text(x + 9, top + 36,
                 doc.ellipsize(caption, "regular", 7, width - 18),
                 "regular", 7, INK_SECONDARY)

    doc.space(height + SECTION_GAP)


def _draw_mood(doc: PdfDocument, data: DashboardData) -> None:
    if not data.moods:
        return

    doc.ensure_space(52)
    doc.text(doc.margin, doc.y + 9, "Настроение отзывов", "bold", 10, INK)
    doc.space(16)

    colors = {1: NEGATIVE, 2: NEUTRAL, 3: POSITIVE}
    bar_top = doc.y
    x = doc.margin
    for bucket in data.moods:
        if not bucket.amount:
            continue
        width = data_width = bucket.share * doc.content_width
        if width <= 0:
            continue
        doc.rect(x, bar_top, max(width - 2, 1), 12, colors.get(bucket.mood_id, NEUTRAL),
                 radius=2)
        x += data_width
    doc.space(18)

    legend_x = doc.margin
    for bucket in data.moods:
        color = colors.get(bucket.mood_id, NEUTRAL)
        name = MOOD_FALLBACK.get(bucket.mood_id, bucket.name)
        label = f"{name}: {bucket.amount} ({format_percent(bucket.share)})"
        doc.rect(legend_x, doc.y + 2, 7, 7, color, radius=2)
        doc.text(legend_x + 11, doc.y + 8, label, "regular", 7.5, INK_SECONDARY)
        legend_x += 11 + doc.width_of(label, "regular", 7.5) + 14

    doc.space(SECTION_GAP)


def _draw_timeline_charts(doc: PdfDocument, data: DashboardData) -> None:
    points = data.timeline
    if not points:
        return

    labels = [point.label for point in points]
    charts = (
        ("Количество отзывов", [
            ("Отзывы", SERIES_1, [float(point.feedbacks) for point in points])
        ], None, "{:.0f}"),
        (f"Средняя оценка отзыва (0–{MAX_RATING})", [
            ("Средняя оценка", SERIES_1, [point.avg_rating for point in points])
        ], float(MAX_RATING), "{:.1f}"),
        ("Позитивные и негативные оценки", [
            ("Позитивные", POSITIVE, [float(point.positives) for point in points]),
            ("Негативные", NEGATIVE, [float(point.negatives) for point in points]),
        ], None, "{:.0f}"),
    )

    for title, series, value_max, value_format in charts:
        _draw_line_chart(doc, title, labels, series, value_max, value_format)


def _draw_line_chart(
    doc: PdfDocument,
    title: str,
    labels: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float | None]]],
    value_max: float | None,
    value_format: str,
) -> None:
    plot_height = 78.0
    block_height = plot_height + 46
    doc.ensure_space(block_height)

    doc.text(doc.margin, doc.y + 9, title, "bold", 10, INK)
    doc.space(16)

    if len(series) > 1:
        legend_x = doc.margin
        for name, color, _ in series:
            doc.rect(legend_x, doc.y, 7, 7, color, radius=2)
            doc.text(legend_x + 11, doc.y + 6, name, "regular", 7.5, INK_SECONDARY)
            legend_x += 14 + doc.width_of(name, "regular", 7.5) + 14
        doc.space(13)

    left = doc.margin + 26
    right = doc.page_width - doc.margin
    top = doc.y
    bottom = top + plot_height

    values = [value for _, _, points in series for value in points if value is not None]
    top_value, ticks = nice_scale(
        value_max if value_max is not None else max(values, default=1.0)
    )

    for tick in ticks:
        y = bottom - (tick / top_value if top_value else 0) * plot_height
        doc.line(left, y, right, y, GRID, 0.4)
        doc.text(left - 5, y + 2.5, tick_text(tick), "regular", 6.5, MUTED, align="right")
    doc.line(left, bottom, right, bottom, AXIS, 0.6)

    count = len(labels)
    step = (right - left) / (count - 1) if count > 1 else 0.0

    def x_at(index: int) -> float:
        return left + (index * step if count > 1 else (right - left) / 2)

    def y_at(value: float) -> float:
        return bottom - (min(value, top_value) / top_value if top_value else 0) * plot_height

    for index in label_indexes(count):
        doc.text(x_at(index), bottom + 10, labels[index], "regular", 6.5, MUTED,
                 align="center")

    for _, color, points in series:
        coordinates = [
            (x_at(index), y_at(value))
            for index, value in enumerate(points)
            if value is not None
        ]
        if not coordinates:
            continue
        doc.polyline(coordinates, color, 1.4)
        if len(coordinates) <= 24:
            for x, y in coordinates:
                doc.circle(x, y, 2.2, color)

        last_index = max(
            index for index, value in enumerate(points) if value is not None
        )
        last_value = points[last_index]
        label_y = y_at(last_value) - 6
        if label_y < top + 6:
            label_y = y_at(last_value) + 12
        doc.text(
            x_at(last_index), label_y, value_format.format(last_value),
            "bold", 7.5, INK,
            align="right" if last_index == count - 1 and count > 1 else "center",
        )

    doc.space(plot_height + 22)


def _draw_bar_section(doc: PdfDocument, data: DashboardData) -> None:
    bottlenecks = [item for item in data.bottlenecks if item.mentions][:8]
    if bottlenecks:
        scale = max(item.mentions for item in bottlenecks) or 1
        _draw_bars(
            doc,
            "Узкие места: критерии с наибольшим числом проблем",
            [
                (
                    item.name,
                    item.responsible,
                    ((item.problems, NEGATIVE), (item.mentions - item.problems, POSITIVE)),
                    scale,
                    f"{item.problems} из {item.mentions}",
                )
                for item in bottlenecks
            ],
            legend=(("Проблемные оценки", NEGATIVE), ("Без замечаний", POSITIVE)),
        )

    divisions = [item for item in data.divisions if item.avg_rating is not None]
    if divisions:
        _draw_bars(
            doc,
            f"Эффективность подразделений: средняя оценка (0–{MAX_RATING})",
            [
                (
                    item.name,
                    item.fio or "—",
                    ((item.avg_rating or 0.0, SERIES_1),),
                    float(MAX_RATING),
                    format_rating(item.avg_rating),
                )
                for item in divisions
            ],
        )


def _draw_bars(
    doc: PdfDocument,
    title: str,
    items: Sequence[tuple[str, str, Sequence[tuple[float, str]], float, str]],
    legend: Sequence[tuple[str, str]] = (),
) -> None:
    doc.ensure_space(46)
    doc.text(doc.margin, doc.y + 9, title, "bold", 10, INK)
    doc.space(15)

    if legend:
        legend_x = doc.margin
        for name, color in legend:
            doc.rect(legend_x, doc.y, 7, 7, color, radius=2)
            doc.text(legend_x + 11, doc.y + 6, name, "regular", 7.5, INK_SECONDARY)
            legend_x += 14 + doc.width_of(name, "regular", 7.5) + 14
        doc.space(13)

    label_width = doc.content_width * 0.34
    value_width = 52.0
    track_left = doc.margin + label_width + 10
    track_width = doc.content_width - label_width - value_width - 20

    for name, sublabel, segments, scale, value_text in items:
        doc.ensure_space(22)
        row_top = doc.y

        doc.text(doc.margin, row_top + 8,
                 doc.ellipsize(name, "regular", 8, label_width), "regular", 8, INK)
        doc.text(doc.margin, row_top + 17,
                 doc.ellipsize(sublabel, "regular", 6.5, label_width),
                 "regular", 6.5, MUTED)

        x = track_left
        for value, color in segments:
            if value <= 0 or scale <= 0:
                continue
            width = min(value / scale, 1.0) * track_width
            doc.rect(x, row_top + 4, max(width - 2, 1), 9, color, radius=2)
            x += width

        doc.text(doc.page_width - doc.margin, row_top + 12, value_text,
                 "regular", 8, INK_SECONDARY, align="right")
        doc.space(22)

    doc.space(SECTION_GAP - 6)


def _draw_table(doc: PdfDocument, section: ReportSection) -> None:
    columns = [width * doc.content_width for width in section.widths]
    offsets: list[float] = []
    cursor = doc.margin
    for width in columns:
        offsets.append(cursor)
        cursor += width

    # Заголовки переносятся на вторую строку, иначе узкие числовые колонки
    # обрезают их до «Упомин…».
    wrapped = [
        doc.wrap(name, "bold", HEAD_SIZE, columns[index] - 8, max_lines=2)
        for index, name in enumerate(section.headers)
    ]
    header_lines = max(len(lines) for lines in wrapped)
    header_height = ROW_HEIGHT + 2 + (header_lines - 1) * 9

    def draw_header() -> None:
        doc.ensure_space(header_height + ROW_HEIGHT)
        top = doc.y
        doc.rect(doc.margin, top, doc.content_width, header_height, "#f7f9fb", radius=2)
        for index, lines in enumerate(wrapped):
            for line_index, line in enumerate(lines):
                _cell(doc, line, offsets[index], columns[index],
                      top + 9 + line_index * 9,
                      section.aligns[index], "bold", HEAD_SIZE, INK_SECONDARY)
        doc.space(header_height + 2)

    doc.ensure_space(ROW_HEIGHT * 4)
    doc.text(doc.margin, doc.y + 10, section.title, "bold", 11, INK)
    doc.space(18)
    draw_header()

    for row in section.rows:
        if doc.y + ROW_HEIGHT > doc.page_height - doc.margin:
            doc.new_page()
            draw_header()

        top = doc.y
        for index, value in enumerate(row):
            _cell(doc, value, offsets[index], columns[index], top + 9,
                  section.aligns[index], "regular", BODY_SIZE, INK)
        doc.line(doc.margin, top + ROW_HEIGHT,
                 doc.page_width - doc.margin, top + ROW_HEIGHT, GRID, 0.4)
        doc.space(ROW_HEIGHT)

    doc.space(SECTION_GAP)


def _cell(
    doc: PdfDocument,
    value: str,
    x: float,
    width: float,
    baseline: float,
    align: str,
    font: str,
    size: float,
    color: str,
) -> None:
    padding = 4.0
    text = doc.ellipsize(value, font, size, width - padding * 2)
    if align == "right":
        doc.text(x + width - padding, baseline, text, font, size, color, align="right")
    else:
        doc.text(x + padding, baseline, text, font, size, color)



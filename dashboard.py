"""
Отрисовка аналитического дашборда sfp_web.py.

Графики строятся как inline SVG и CSS-полосы: внешних библиотек нет, страница
работает без интернета. Палитра — проверенная категориальная схема (слоты 1–3)
и расходящаяся пара «синий ↔ красный» с нейтральным серым.
"""

from __future__ import annotations

import html
import math
from typing import Iterable, Sequence

from analytics import (
    DashboardData,
    MAX_RATING,
    MoodBucket,
    PERIOD_PRESETS,
    TaskStat,
    Totals,
)

# Палитра проверена scripts/validate_palette.js на поверхности #ffffff:
# полюса расходящейся пары проходят все гейты (CVD ΔE 21.6, контраст ≥ 3:1).
# Номинальные категории (критерии, подразделения) намеренно красятся одним
# цветом: градиент по величине дублировал бы длину полосы.
SURFACE = "#ffffff"
INK = "#1f2933"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

SERIES_1 = "#2a78d6"  # синий — категориальный слот 1
POSITIVE = "#2a78d6"  # полюс «позитив»
NEGATIVE = "#e34948"  # полюс «негатив»
NEUTRAL = "#898781"   # нейтральная середина расходящейся шкалы

# Геометрия SVG-графиков.
CHART_WIDTH = 560
CHART_HEIGHT = 240
PLOT_LEFT = 46
PLOT_RIGHT = CHART_WIDTH - 18
PLOT_TOP = 18
PLOT_BOTTOM = CHART_HEIGHT - 38

MAX_BOTTLENECK_BARS = 8
MAX_TASK_ROWS = 8


DASHBOARD_STYLE = """
.filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
           background: #fff; border: 1px solid #e1e6eb; border-radius: 10px;
           padding: 12px 16px; margin-bottom: 20px; }
.filters .chip { padding: 6px 12px; border-radius: 20px; font-size: 13px;
                 text-decoration: none; color: #52606d; background: #f1f4f7; }
.filters .chip.active { background: #2a78d6; color: #fff; }
.filters form { display: flex; gap: 8px; align-items: center; margin-left: auto; }
.filters input[type=date] { font-size: 13px; padding: 5px 8px; border-radius: 6px;
                            border: 1px solid #d4dae1; }
.filters .range { font-size: 13px; color: #898781; }

.tiles { display: grid; gap: 14px; margin-bottom: 24px;
         grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
.tile { background: #fff; border: 1px solid #e1e6eb; border-radius: 10px;
        padding: 16px 18px; }
.tile b { display: block; font-size: 30px; line-height: 1.15; color: #1f2933;
          font-weight: 600; }
.tile span { display: block; font-size: 13px; color: #52606d; margin-top: 4px; }
.tile i { display: block; font-size: 12px; color: #898781; font-style: normal;
          margin-top: 6px; }

.cards { display: grid; gap: 16px; margin-bottom: 26px;
         grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); }
.card { background: #fff; border: 1px solid #e1e6eb; border-radius: 10px;
        padding: 18px 20px 14px; }
.card h3 { margin: 0 0 2px; font-size: 15px; font-weight: 600; }
.card .hint { margin: 0 0 14px; font-size: 12px; color: #898781; }
.card svg { display: block; width: 100%; height: auto; }

.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 0 0 12px;
          font-size: 12px; color: #52606d; }
.legend em { font-style: normal; display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

.stack { display: flex; height: 22px; margin: 6px 0 10px; }
.stack span { height: 100%; border-radius: 4px; margin-right: 2px; }
.stack span:last-child { margin-right: 0; }

.meters { display: grid; gap: 14px; }
.meter b { display: flex; justify-content: space-between; font-size: 13px;
           font-weight: 500; color: #1f2933; margin-bottom: 6px; }
.meter b em { font-style: normal; color: #52606d; font-variant-numeric: tabular-nums; }
.meter .track { height: 8px; background: #eceff3; border-radius: 4px; }
.meter .track i { display: block; height: 100%; border-radius: 4px;
                  background: #2a78d6; }

.bars { display: grid; gap: 12px; margin-bottom: 4px; }
.bar { display: grid; grid-template-columns: minmax(120px, 34%) 1fr auto;
       gap: 12px; align-items: center; }
.bar .name { font-size: 13px; color: #1f2933; line-height: 1.3; }
.bar .name em { display: block; font-style: normal; font-size: 11px; color: #898781; }
.bar .track { display: flex; height: 18px; }
.bar .track span { height: 100%; border-radius: 4px; margin-right: 2px; }
.bar .track span:last-child { margin-right: 0; }
.bar .value { font-size: 13px; color: #52606d; white-space: nowrap;
              font-variant-numeric: tabular-nums; }

details.table-view { margin-top: 12px; }
details.table-view summary { font-size: 12px; color: #2f6fed; cursor: pointer; }
details.table-view table { margin-top: 10px; }

.section { margin-bottom: 30px; }
.section > h2 { font-size: 18px; margin: 0 0 4px; }
.section > p.hint { margin: 0 0 14px; font-size: 13px; color: #6b7785; }
.nowrap { white-space: nowrap; font-variant-numeric: tabular-nums; }
.bad { color: #c02f2f; }
.note { background: #fff; border: 1px solid #e1e6eb; border-left: 3px solid #eda100;
        border-radius: 8px; padding: 12px 16px; font-size: 13px; color: #52606d;
        margin-bottom: 20px; }
"""


# ---------------------------------------------------------------------------
# Страница
# ---------------------------------------------------------------------------


def render_dashboard(data: DashboardData, message: str = "") -> str:
    parts = [
        '<p><a href="/">← К списку отзывов</a></p>',
        "<h1>Аналитический дашборд</h1>",
        '<p class="subtitle">Показатели рассчитаны по обработанным отзывам '
        "и справочникам критериев качества, услуг и подразделений.</p>",
    ]
    if message:
        parts.append(f'<div class="flash err">{html.escape(message)}</div>')
    parts.append(_render_filters(data))

    if not data.has_data:
        parts.append(_render_empty(data))
        return "".join(parts)

    if data.unprocessed or data.undated:
        parts.append(_render_note(data))

    parts.append(_render_summary(data))
    parts.append(_render_timeline(data))
    parts.append(_render_bottlenecks(data))
    parts.append(_render_divisions(data))
    return "".join(parts)


def _render_empty(data: DashboardData) -> str:
    reason = (
        "В базе нет обработанных отзывов — загрузите CSV и запустите обработку."
        if not data.unprocessed
        else f"За выбранный период нет обработанных отзывов "
        f"(необработанных в базе: {data.unprocessed})."
    )
    return f'<div class="empty">{html.escape(reason)}</div>'


def _render_note(data: DashboardData) -> str:
    notes: list[str] = []
    if data.unprocessed:
        notes.append(f"не обработано отзывов — {data.unprocessed}")
    if data.undated:
        notes.append(
            f"без распознанной даты — {data.undated} "
            "(учтены в сводке, но не в динамике)"
        )
    return f'<div class="note">В расчёт не вошли: {html.escape("; ".join(notes))}.</div>'


def _render_filters(data: DashboardData) -> str:
    chips: list[str] = []
    for preset, label in PERIOD_PRESETS:
        if preset == "custom":
            continue
        active = " active" if data.period.preset == preset else ""
        chips.append(
            f'<a class="chip{active}" href="/dashboard?period={preset}">'
            f"{html.escape(label)}</a>"
        )

    date_from = data.period.date_from.isoformat() if data.period.date_from else ""
    date_to = data.period.date_to.isoformat() if data.period.date_to else ""
    custom_active = " active" if data.period.preset == "custom" else ""
    query = _period_query(data)

    return f"""
    <div class="filters">
      {"".join(chips)}
      <span class="range">Диапазон: {html.escape(data.period.label())}</span>
      <form method="get" action="/dashboard">
        <input type="hidden" name="period" value="custom">
        <input type="date" name="from" value="{date_from}" aria-label="Дата с">
        <input type="date" name="to" value="{date_to}" aria-label="Дата по">
        <button type="submit" class="secondary{custom_active}">Применить</button>
      </form>
    </div>
    <div class="filters">
      <span class="range">Выгрузить отчёт за выбранный период:</span>
      <a class="button secondary" href="/dashboard.csv{query}">Скачать CSV</a>
      <a class="button secondary" href="/dashboard.pdf{query}">Скачать PDF</a>
    </div>
    """


def _period_query(data: DashboardData) -> str:
    """Строка запроса, повторяющая текущий фильтр, — для ссылок выгрузки."""
    parts = [f"period={data.period.preset}"]
    if data.period.preset == "custom":
        if data.period.date_from:
            parts.append(f"from={data.period.date_from.isoformat()}")
        if data.period.date_to:
            parts.append(f"to={data.period.date_to.isoformat()}")
    return "?" + "&amp;".join(parts)


# ---------------------------------------------------------------------------
# 1. Сводная статистика
# ---------------------------------------------------------------------------


def _render_summary(data: DashboardData) -> str:
    totals = data.totals
    tiles = [
        _tile(str(totals.feedbacks), "Отзывов проанализировано",
              f"вопросов от клиентов — {totals.questions}"),
        _tile(format_rating(totals.avg_rating), "Средняя оценка отзыва",
              f"по шкале 0–{MAX_RATING}, оценено {totals.rated_feedbacks}"),
        _tile(str(totals.quality_mentions), "Оценок критериев качества",
              f"из них проблемных — {totals.problem_mentions}"),
        _tile(format_percent(totals.problem_share), "Доля проблемных оценок",
              "оценка от 1 до 3 баллов"),
        _tile(str(totals.tasks), "Задач в работу",
              f"запросов от клиентов — {totals.client_requests}"),
    ]

    cards = [
        f"""<section class="card">
          <h3>Настроение отзывов</h3>
          <p class="hint">Доли по шкале настроения из справочника items_mood.</p>
          {_render_mood(data.moods)}
        </section>""",
        f"""<section class="card">
          <h3>Характер обращений</h3>
          <p class="hint">Доля отзывов с соответствующим признаком.</p>
          {_render_meters(totals)}
        </section>""",
        f"""<section class="card">
          <h3>Позитив и негатив</h3>
          <p class="hint">Общее число упомянутых элементов продукта.</p>
          {_render_polarity(totals)}
        </section>""",
    ]

    return (
        '<section class="section"><h2>Сводная статистика</h2>'
        '<p class="hint">Агрегаты по всем аналитическим показателям '
        "за выбранный период.</p>"
        f'<div class="tiles">{"".join(tiles)}</div>'
        f'<div class="cards">{"".join(cards)}</div></section>'
    )


def _tile(value: str, caption: str, footnote: str = "") -> str:
    note = f"<i>{html.escape(footnote)}</i>" if footnote else ""
    return (
        f'<div class="tile"><b>{html.escape(value)}</b>'
        f"<span>{html.escape(caption)}</span>{note}</div>"
    )


def _render_mood(moods: Sequence[MoodBucket]) -> str:
    if not moods:
        return '<p class="hint">Настроение не определено ни в одном отзыве.</p>'

    colors = {1: NEGATIVE, 2: NEUTRAL, 3: POSITIVE}
    segments: list[str] = []
    legend: list[str] = []

    for bucket in moods:
        color = colors.get(bucket.mood_id, NEUTRAL)
        if bucket.amount:
            segments.append(
                f'<span style="width:{bucket.share * 100:.2f}%;background:{color}"'
                f' title="{html.escape(bucket.name)}: {bucket.amount}"></span>'
            )
        legend.append(
            f'<em><i style="background:{color}"></i>'
            f"{html.escape(shorten(bucket.name, 34))} — "
            f"{bucket.amount} ({format_percent(bucket.share)})</em>"
        )

    rows = "".join(
        f"<tr><td>{html.escape(bucket.name)}</td>"
        f'<td class="nowrap">{bucket.amount}</td>'
        f'<td class="nowrap">{format_percent(bucket.share)}</td></tr>'
        for bucket in moods
    )

    return (
        f'<div class="stack">{"".join(segments)}</div>'
        f'<div class="legend">{"".join(legend)}</div>'
        + _table_view(
            "Таблица значений",
            "<tr><th>Настроение</th><th>Отзывов</th><th>Доля</th></tr>",
            rows,
        )
    )


def _render_meters(totals: Totals) -> str:
    if not totals.feedbacks:
        return ""

    rows = (
        ("Эмоциональные", totals.emotional),
        ("Грубая форма", totals.rude),
        ("Неформальный стиль", totals.informal),
    )
    meters = "".join(
        f'<div class="meter"><b>{html.escape(name)}'
        f"<em>{amount} · {format_percent(amount / totals.feedbacks)}</em></b>"
        f'<div class="track"><i style="width:'
        f'{amount / totals.feedbacks * 100:.2f}%"></i></div></div>'
        for name, amount in rows
    )
    return f'<div class="meters">{meters}</div>'


def _render_polarity(totals: Totals) -> str:
    total = totals.positives + totals.negatives
    if not total:
        return '<p class="hint">Оценки элементов продукта не найдены.</p>'

    positive_share = totals.positives / total
    return (
        '<div class="stack">'
        f'<span style="width:{positive_share * 100:.2f}%;background:{POSITIVE}"'
        f' title="Позитивные: {totals.positives}"></span>'
        f'<span style="width:{(1 - positive_share) * 100:.2f}%;background:{NEGATIVE}"'
        f' title="Негативные: {totals.negatives}"></span>'
        "</div>"
        '<div class="legend">'
        f'<em><i style="background:{POSITIVE}"></i>Позитивные — '
        f"{totals.positives} ({format_percent(positive_share)})</em>"
        f'<em><i style="background:{NEGATIVE}"></i>Негативные — '
        f"{totals.negatives} ({format_percent(1 - positive_share)})</em>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# 2. Динамика за период
# ---------------------------------------------------------------------------

GRANULARITY_LABELS = {"day": "по дням", "month": "по месяцам", "year": "по годам"}


def _render_timeline(data: DashboardData) -> str:
    points = data.timeline
    if not points:
        return (
            '<section class="section"><h2>Динамика показателей</h2>'
            '<div class="empty">Не удалось распознать даты отзывов — '
            "динамику построить не по чему.</div></section>"
        )

    labels = [point.label for point in points]
    scale = GRANULARITY_LABELS.get(data.granularity, "")

    volume = _line_chart(
        labels,
        [{"name": "Отзывы", "color": SERIES_1,
          "values": [float(point.feedbacks) for point in points]}],
        value_format="{:.0f}",
    )
    rating = _line_chart(
        labels,
        [{"name": "Средняя оценка", "color": SERIES_1,
          "values": [point.avg_rating for point in points]}],
        value_max=float(MAX_RATING),
        value_format="{:.1f}",
    )
    polarity = _line_chart(
        labels,
        [
            {"name": "Позитивные", "color": POSITIVE,
             "values": [float(point.positives) for point in points]},
            {"name": "Негативные", "color": NEGATIVE,
             "values": [float(point.negatives) for point in points]},
        ],
        value_format="{:.0f}",
    )

    table = _table_view(
        "Таблица значений",
        "<tr><th>Период</th><th>Отзывов</th><th>Средняя оценка</th>"
        "<th>Позитивных</th><th>Негативных</th><th>Проблемных оценок</th></tr>",
        "".join(
            f"<tr><td>{html.escape(point.label)}</td>"
            f'<td class="nowrap">{point.feedbacks}</td>'
            f'<td class="nowrap">{format_rating(point.avg_rating)}</td>'
            f'<td class="nowrap">{point.positives}</td>'
            f'<td class="nowrap">{point.negatives}</td>'
            f'<td class="nowrap">{point.problem_mentions}</td></tr>'
            for point in points
        ),
    )

    cards = (
        f"""<section class="card">
          <h3>Количество отзывов</h3>
          <p class="hint">Сколько отзывов пришлось на период, {scale}.</p>
          {volume}
        </section>"""
        f"""<section class="card">
          <h3>Средняя оценка отзыва</h3>
          <p class="hint">Шкала 0–{MAX_RATING} баллов.</p>
          {rating}
        </section>"""
        f"""<section class="card">
          <h3>Позитивные и негативные оценки</h3>
          {_legend([("Позитивные", POSITIVE), ("Негативные", NEGATIVE)])}
          {polarity}
        </section>"""
    )

    return (
        '<section class="section"><h2>Динамика показателей за период</h2>'
        f'<p class="hint">Группировка {html.escape(scale)}; '
        "масштаб выбирается по длине периода.</p>"
        f'<div class="cards">{cards}</div>{table}</section>'
    )


# ---------------------------------------------------------------------------
# 3. Узкие места
# ---------------------------------------------------------------------------


def _render_bottlenecks(data: DashboardData) -> str:
    items = [item for item in data.bottlenecks if item.mentions]
    if not items:
        return (
            '<section class="section"><h2>Узкие места</h2>'
            '<div class="empty">Критерии качества в отзывах не упоминались.'
            "</div></section>"
        )

    top = items[:MAX_BOTTLENECK_BARS]
    scale = max(item.mentions for item in top) or 1

    bars = "".join(
        f"""<div class="bar">
          <div class="name">{html.escape(shorten(item.name, 58))}
            <em>{html.escape(item.responsible)}</em></div>
          <div class="track">
            {_segment(item.problems, scale, NEGATIVE,
                      f"Проблемных оценок: {item.problems}")}
            {_segment(item.mentions - item.problems, scale, POSITIVE,
                      f"Без замечаний: {item.mentions - item.problems}")}
          </div>
          <div class="value">{item.problems} из {item.mentions}</div>
        </div>"""
        for item in top
    )

    rows = "".join(
        f"<tr><td>{html.escape(item.name)}</td>"
        f"<td>{html.escape(item.responsible)}</td>"
        f'<td class="nowrap">{item.mentions}</td>'
        f'<td class="nowrap{" bad" if item.problems else ""}">{item.problems}</td>'
        f'<td class="nowrap">{format_percent(item.problem_share)}</td>'
        f'<td class="nowrap">{format_rating(item.avg_rating)}</td>'
        f'<td class="nowrap">{item.tasks}</td></tr>'
        for item in items
    )

    tasks = _render_tasks(data.tasks)

    return (
        '<section class="section"><h2>Узкие места</h2>'
        '<p class="hint">Критерии качества, ранжированные по числу проблемных '
        "оценок (от 1 до 3 баллов).</p>"
        '<div class="cards"><section class="card">'
        f"<h3>Топ-{len(top)} критериев по числу проблем</h3>"
        f'{_legend([("Проблемные оценки", NEGATIVE), ("Без замечаний", POSITIVE)])}'
        f'<div class="bars">{bars}</div>'
        "</section>"
        f"{tasks}</div>"
        + _table_full(
            "<tr><th>Критерий качества</th><th>Подразделение</th><th>Упоминаний</th>"
            "<th>Проблемных</th><th>Доля проблем</th><th>Средняя оценка</th>"
            "<th>Задач</th></tr>",
            rows,
        )
        + "</section>"
    )


def _render_tasks(tasks: Sequence[TaskStat]) -> str:
    if not tasks:
        return ""

    top = tasks[:MAX_TASK_ROWS]
    scale = max(item.amount for item in top) or 1
    bars = "".join(
        f"""<div class="bar">
          <div class="name">{html.escape(shorten(item.name, 52))}
            <em>{html.escape(item.responsible)}</em></div>
          <div class="track">
            {_segment(item.amount, scale, SERIES_1,
                      f"Задач: {item.amount}")}
          </div>
          <div class="value">{item.amount}</div>
        </div>"""
        for item in top
    )
    return (
        '<section class="card"><h3>Задачи, поставленные по отзывам</h3>'
        '<p class="hint">Классификация по справочнику типовых услуг.</p>'
        f'<div class="bars">{bars}</div></section>'
    )


# ---------------------------------------------------------------------------
# 4. Эффективность подразделений
# ---------------------------------------------------------------------------


def _render_divisions(data: DashboardData) -> str:
    divisions = [item for item in data.divisions if item.mentions or item.assigned_feedbacks]
    if not divisions:
        return (
            '<section class="section"><h2>Эффективность подразделений</h2>'
            '<div class="empty">Нет данных для привязки оценок к подразделениям.'
            "</div></section>"
        )

    rated = [item for item in divisions if item.avg_rating is not None]
    bars = "".join(
        f"""<div class="bar">
          <div class="name">{html.escape(shorten(item.name, 52))}
            <em>{html.escape(item.fio or "—")}</em></div>
          <div class="track">
            {_segment(item.avg_rating or 0.0, float(MAX_RATING), SERIES_1,
                      f"Средняя оценка: {format_rating(item.avg_rating)} из {MAX_RATING}")}
          </div>
          <div class="value">{format_rating(item.avg_rating)}</div>
        </div>"""
        for item in rated
    )

    chart = (
        '<section class="card">'
        f"<h3>Средняя оценка по своим критериям</h3>"
        f'<p class="hint">Шкала 0–{MAX_RATING} баллов, больше — лучше.</p>'
        f'<div class="bars">{bars}</div></section>'
        if rated
        else ""
    )

    problem_items = [item for item in divisions if item.mentions]
    problem_scale = max((item.problem_share for item in problem_items), default=0.0) or 1.0
    problems = "".join(
        f"""<div class="bar">
          <div class="name">{html.escape(shorten(item.name, 52))}
            <em>{item.problems} из {item.mentions} оценок</em></div>
          <div class="track">
            {_segment(item.problem_share, problem_scale, NEGATIVE,
                      f"Доля проблемных оценок: {format_percent(item.problem_share)}")}
          </div>
          <div class="value">{format_percent(item.problem_share)}</div>
        </div>"""
        for item in sorted(problem_items, key=lambda item: -item.problem_share)
    )

    problem_card = (
        '<section class="card"><h3>Доля проблемных оценок</h3>'
        '<p class="hint">Меньше — лучше; длина полосы нормирована на максимум.</p>'
        f'<div class="bars">{problems}</div></section>'
        if problem_items
        else ""
    )

    rows = "".join(
        f"<tr><td>{html.escape(item.name)}</td>"
        f"<td>{html.escape(item.fio or '—')}</td>"
        f'<td class="nowrap">{item.assigned_feedbacks}</td>'
        f'<td class="nowrap">{item.mentions}</td>'
        f'<td class="nowrap{" bad" if item.problems else ""}">{item.problems}</td>'
        f'<td class="nowrap">{format_percent(item.problem_share)}</td>'
        f'<td class="nowrap">{format_rating(item.avg_rating)}</td>'
        f'<td class="nowrap">{item.tasks}</td></tr>'
        for item in divisions
    )

    return (
        '<section class="section"><h2>Эффективность подразделений</h2>'
        '<p class="hint">Критерии качества связаны с подразделениями через '
        "справочник items_quality, задачи — через items_services.</p>"
        f'<div class="cards">{chart}{problem_card}</div>'
        + _table_full(
            "<tr><th>Подразделение</th><th>Руководитель</th><th>Отзывов закреплено</th>"
            "<th>Оценок</th><th>Проблемных</th><th>Доля проблем</th>"
            "<th>Средняя оценка</th><th>Задач</th></tr>",
            rows,
        )
        + "</section>"
    )


# ---------------------------------------------------------------------------
# Примитивы графиков
# ---------------------------------------------------------------------------


def _line_chart(
    labels: Sequence[str],
    series: Sequence[dict],
    value_max: float | None = None,
    value_format: str = "{:.0f}",
) -> str:
    """Линейный график: 2px линии, маркеры 8px с кольцом фона, подсказки по осям."""
    values = [
        value
        for item in series
        for value in item["values"]
        if value is not None
    ]
    top, ticks = nice_scale(
        value_max if value_max is not None else max(values, default=1.0)
    )

    plot_width = PLOT_RIGHT - PLOT_LEFT
    plot_height = PLOT_BOTTOM - PLOT_TOP
    count = len(labels)
    step = plot_width / (count - 1) if count > 1 else 0.0

    def x_at(index: int) -> float:
        return PLOT_LEFT + (index * step if count > 1 else plot_width / 2)

    def y_at(value: float) -> float:
        return PLOT_BOTTOM - (min(value, top) / top if top else 0) * plot_height

    parts: list[str] = [
        f'<svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" role="img" '
        f'preserveAspectRatio="xMidYMid meet">'
    ]

    for tick in ticks:
        y = y_at(tick)
        parts.append(
            f'<line x1="{PLOT_LEFT}" y1="{y:.1f}" x2="{PLOT_RIGHT}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PLOT_LEFT - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="{MUTED}" '
            f'style="font-variant-numeric:tabular-nums">{tick_text(tick)}</text>'
        )

    parts.append(
        f'<line x1="{PLOT_LEFT}" y1="{PLOT_BOTTOM}" x2="{PLOT_RIGHT}" '
        f'y2="{PLOT_BOTTOM}" stroke="{AXIS}" stroke-width="1"/>'
    )

    for index in label_indexes(count):
        parts.append(
            f'<text x="{x_at(index):.1f}" y="{PLOT_BOTTOM + 18}" '
            f'text-anchor="middle" font-size="11" fill="{MUTED}">'
            f"{html.escape(labels[index])}</text>"
        )

    endpoints: list[tuple[float, float, str, str]] = []

    for item in series:
        points = [
            (x_at(index), y_at(value))
            for index, value in enumerate(item["values"])
            if value is not None
        ]
        if not points:
            continue

        if len(points) > 1:
            polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            parts.append(
                f'<polyline points="{polyline}" fill="none" '
                f'stroke="{item["color"]}" stroke-width="2" '
                'stroke-linejoin="round" stroke-linecap="round"/>'
            )

        if len(points) <= 24:
            for x, y in points:
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" '
                    f'fill="{item["color"]}" stroke="{SURFACE}" stroke-width="2"/>'
                )

        last_index = max(
            index for index, value in enumerate(item["values"]) if value is not None
        )
        last_value = item["values"][last_index]
        endpoints.append(
            (
                x_at(last_index),
                y_at(last_value),
                value_format.format(last_value),
                "end" if last_index == count - 1 and count > 1 else "middle",
            )
        )

    # Прямые подписи конечных значений — текстовым цветом, не цветом серии.
    # Цвет несёт маркер рядом; подписи разводятся, чтобы не слипаться и не
    # обрезаться о верхний край холста.
    for x, y, text, anchor in _place_labels(endpoints):
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'font-size="12" font-weight="600" fill="{INK}">'
            f"{html.escape(text)}</text>"
        )

    # Прозрачные зоны наведения шириной не меньше 24px с подсказкой по всем сериям.
    band = max(step, 24.0) if count > 1 else plot_width
    for index, label in enumerate(labels):
        lines = [label]
        for item in series:
            value = item["values"][index]
            text = "—" if value is None else value_format.format(value)
            lines.append(f"{item['name']}: {text}")
        parts.append(
            f'<rect x="{x_at(index) - band / 2:.1f}" y="{PLOT_TOP}" '
            f'width="{band:.1f}" height="{plot_height:.1f}" fill="transparent">'
            f"<title>{html.escape(chr(10).join(lines))}</title></rect>"
        )

    parts.append("</svg>")
    return "".join(parts)


LABEL_FONT_SIZE = 12
LABEL_GAP = 12
LABEL_LINE = 14


def _place_labels(
    endpoints: Sequence[tuple[float, float, str, str]],
) -> list[tuple[float, float, str, str]]:
    """
    Расставляет подписи конечных точек.

    Подпись ставится над точкой; если сверху не хватает места до края холста —
    под точкой. Совпадающие по высоте подписи разводятся построчно.
    """
    ascent = LABEL_FONT_SIZE * 0.78
    placed: list[tuple[float, float, str, str]] = []
    taken: list[float] = []

    for x, y, text, anchor in sorted(endpoints, key=lambda item: item[1]):
        baseline = y - LABEL_GAP
        if baseline - ascent < PLOT_TOP:
            baseline = y + LABEL_GAP + ascent

        while any(abs(baseline - used) < LABEL_LINE for used in taken):
            baseline += LABEL_LINE

        if baseline > PLOT_BOTTOM:
            baseline = min(y - LABEL_GAP, PLOT_BOTTOM)

        taken.append(baseline)
        placed.append((x, baseline, text, anchor))

    return placed


def _segment(value: float, scale: float, color: str, tooltip: str) -> str:
    if value <= 0 or scale <= 0:
        return ""
    width = min(value / scale, 1.0) * 100
    return (
        f'<span style="width:{width:.2f}%;background:{color}" '
        f'title="{html.escape(tooltip)}"></span>'
    )


def _legend(entries: Iterable[tuple[str, str]]) -> str:
    items = "".join(
        f'<em><i style="background:{color}"></i>{html.escape(name)}</em>'
        for name, color in entries
    )
    return f'<div class="legend">{items}</div>'


def _table_view(summary: str, head: str, rows: str) -> str:
    return (
        f'<details class="table-view"><summary>{html.escape(summary)}</summary>'
        f"<table><thead>{head}</thead><tbody>{rows}</tbody></table></details>"
    )


def _table_full(head: str, rows: str) -> str:
    return f"<table><thead>{head}</thead><tbody>{rows}</tbody></table>"


TICK_STEPS = (
    0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000,
)


def nice_scale(max_value: float, max_ticks: int = 4) -> tuple[float, list[float]]:
    """
    Верх шкалы и деления к нему.

    Шаг берётся из «круглого» набора, поэтому у целочисленных счётчиков
    подписи тоже целые — без 0.7 и 1.3 на оси.
    """
    if max_value <= 0:
        return 1.0, [0.0, 1.0]

    for step in TICK_STEPS:
        if max_value / step <= max_ticks - 1:
            count = math.ceil(max_value / step - 1e-9)
            return step * count, [step * index for index in range(count + 1)]

    step = 10 ** (len(str(int(max_value))) - 1)
    count = math.ceil(max_value / step)
    return float(step * count), [float(step * index) for index in range(count + 1)]


def tick_text(value: float) -> str:
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def label_indexes(count: int) -> list[int]:
    if count <= 6:
        return list(range(count))
    stride = (count + 5) // 6
    indexes = list(range(0, count, stride))
    if indexes[-1] != count - 1:
        indexes.append(count - 1)
    return indexes


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------


def format_rating(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def format_percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def shorten(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"

"""
Аналитика по обработанным отзывам для дашборда sfp_web.py.

Модуль не зависит от web-слоя: на вход — строки таблицы отзывов и справочники
из каталога data, на выходе — посчитанные показатели.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Шкала оценок критериев качества (см. prompts.base_parse_prompt в settings.ini):
# 0 — оценки нет, 1 очень плохо … 5 отлично, 6 выше ожиданий.
NO_RATING = 0
PROBLEM_RATING_MAX = 3
MAX_RATING = 6

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y")

MONTH_LABELS = (
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)

MOOD_FALLBACK = {
    1: "Негатив перевешивает",
    2: "Взвешенная оценка",
    3: "Позитив перевешивает",
}


# ---------------------------------------------------------------------------
# Справочники
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityItem:
    id: int
    name: str
    importance: int
    responsible_id: int | None


@dataclass(frozen=True)
class ServiceItem:
    id: int
    name: str
    responsible_id: int | None


@dataclass(frozen=True)
class Responsible:
    id: int
    name: str
    position: str
    fio: str


@dataclass(frozen=True)
class ReferenceData:
    quality: Mapping[int, QualityItem]
    services: Mapping[int, ServiceItem]
    responsibles: Mapping[int, Responsible]
    moods: Mapping[int, str]

    def responsible_name(self, responsible_id: int | None) -> str:
        if responsible_id is None:
            return "Не определено"
        found = self.responsibles.get(responsible_id)
        return found.name if found else f"Подразделение {responsible_id}"

    def mood_name(self, mood_id: int) -> str:
        return self.moods.get(mood_id) or MOOD_FALLBACK.get(mood_id, f"Настроение {mood_id}")


def load_reference_data(data_dir: Path) -> ReferenceData:
    """Читает справочники из каталога настроек (те же файлы, что и у промптов)."""
    quality = {
        item_id: QualityItem(
            id=item_id,
            name=row.get("name", ""),
            importance=_safe_int(row.get("importance")) or 1,
            responsible_id=_safe_int(row.get("responsible_id")),
        )
        for item_id, row in _read_reference_rows(data_dir / "items_quality.csv")
    }
    services = {
        item_id: ServiceItem(
            id=item_id,
            name=row.get("name", ""),
            responsible_id=_safe_int(row.get("responsible_id")),
        )
        for item_id, row in _read_reference_rows(data_dir / "items_services.csv")
    }
    responsibles = {
        item_id: Responsible(
            id=item_id,
            name=row.get("name", ""),
            position=row.get("position", ""),
            fio=row.get("fio", ""),
        )
        for item_id, row in _read_reference_rows(data_dir / "items_responsibles.csv")
    }
    moods = {
        item_id: row.get("mood", "")
        for item_id, row in _read_reference_rows(data_dir / "items_mood.csv")
    }
    return ReferenceData(quality, services, responsibles, moods)


def _read_reference_rows(csv_path: Path) -> list[tuple[int, dict[str, str]]]:
    if not csv_path.exists():
        return []

    rows: list[tuple[int, dict[str, str]]] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        for row in csv.DictReader(csv_file, delimiter=";"):
            item_id = _safe_int(row.get("id"))
            if item_id is None:
                continue
            cleaned = {
                key: (value or "").strip()
                for key, value in row.items()
                if key
            }
            rows.append((item_id, cleaned))
    return rows


# ---------------------------------------------------------------------------
# Результаты расчётов
# ---------------------------------------------------------------------------


@dataclass
class Period:
    """Выбранный период и его границы."""

    preset: str = "all"
    date_from: date | None = None
    date_to: date | None = None
    data_from: date | None = None
    data_to: date | None = None

    @property
    def is_filtered(self) -> bool:
        return self.date_from is not None or self.date_to is not None

    def label(self) -> str:
        start = self.date_from or self.data_from
        end = self.date_to or self.data_to
        if start is None or end is None:
            return "период не определён"
        return f"{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}"


@dataclass
class Totals:
    feedbacks: int = 0
    rated_feedbacks: int = 0
    avg_rating: float | None = None
    positives: int = 0
    negatives: int = 0
    questions: int = 0
    quality_mentions: int = 0
    problem_mentions: int = 0
    tasks: int = 0
    client_requests: int = 0
    emotional: int = 0
    rude: int = 0
    informal: int = 0

    @property
    def problem_share(self) -> float:
        return self.problem_mentions / self.quality_mentions if self.quality_mentions else 0.0


@dataclass
class MoodBucket:
    mood_id: int
    name: str
    amount: int
    share: float


@dataclass
class TimelinePoint:
    key: str
    label: str
    feedbacks: int = 0
    positives: int = 0
    negatives: int = 0
    problem_mentions: int = 0
    rating_sum: float = 0.0
    rating_count: int = 0

    @property
    def avg_rating(self) -> float | None:
        return self.rating_sum / self.rating_count if self.rating_count else None


@dataclass
class Bottleneck:
    key: str
    name: str
    responsible: str
    mentions: int = 0
    problems: int = 0
    rating_sum: int = 0
    rating_count: int = 0
    tasks: int = 0
    known: bool = True

    @property
    def avg_rating(self) -> float | None:
        return self.rating_sum / self.rating_count if self.rating_count else None

    @property
    def problem_share(self) -> float:
        return self.problems / self.mentions if self.mentions else 0.0


@dataclass
class DivisionStat:
    id: int
    name: str
    fio: str = ""
    assigned_feedbacks: int = 0
    mentions: int = 0
    problems: int = 0
    rating_sum: int = 0
    rating_count: int = 0
    tasks: int = 0

    @property
    def avg_rating(self) -> float | None:
        return self.rating_sum / self.rating_count if self.rating_count else None

    @property
    def problem_share(self) -> float:
        return self.problems / self.mentions if self.mentions else 0.0


@dataclass
class TaskStat:
    name: str
    responsible: str
    amount: int


@dataclass
class DashboardData:
    period: Period = field(default_factory=Period)
    granularity: str = "month"
    totals: Totals = field(default_factory=Totals)
    moods: list[MoodBucket] = field(default_factory=list)
    timeline: list[TimelinePoint] = field(default_factory=list)
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    divisions: list[DivisionStat] = field(default_factory=list)
    tasks: list[TaskStat] = field(default_factory=list)
    undated: int = 0
    unprocessed: int = 0

    @property
    def has_data(self) -> bool:
        return self.totals.feedbacks > 0


# ---------------------------------------------------------------------------
# Расчёт
# ---------------------------------------------------------------------------


def build_dashboard(
    rows: Iterable[Mapping[str, Any]],
    references: ReferenceData,
    preset: str = "all",
    date_from: date | None = None,
    date_to: date | None = None,
) -> DashboardData:
    """
    Считает все показатели дашборда по обработанным отзывам.

    В расчёт попадают только отзывы со статусом «Обработан» — у остальных
    нет результата анализа.
    """
    processed: list[tuple[Mapping[str, Any], dict[str, Any], date | None]] = []
    unprocessed = 0

    for row in rows:
        analysis = _load_json_object(row["analysis_json"])
        if not analysis:
            unprocessed += 1
            continue
        processed.append((row, analysis, parse_feedback_date(row["feedback_date"])))

    known_dates = [item[2] for item in processed if item[2] is not None]
    period = Period(
        preset=preset,
        data_from=min(known_dates) if known_dates else None,
        data_to=max(known_dates) if known_dates else None,
    )
    period.date_from, period.date_to = resolve_period(
        preset,
        date_from,
        date_to,
        period.data_to,
    )

    selected: list[tuple[Mapping[str, Any], dict[str, Any], date | None]] = []
    undated = 0
    for row, analysis, moment in processed:
        if moment is None:
            undated += 1
            if period.is_filtered:
                continue
        elif period.date_from is not None and moment < period.date_from:
            continue
        elif period.date_to is not None and moment > period.date_to:
            continue
        selected.append((row, analysis, moment))

    data = DashboardData(
        period=period,
        unprocessed=unprocessed,
        undated=undated if not period.is_filtered else 0,
    )
    if not selected:
        return data

    data.totals = _build_totals(selected)
    data.moods = _build_moods(selected, references)
    data.granularity = _choose_granularity([m for _, _, m in selected if m])
    data.timeline = _build_timeline(selected, data.granularity)
    data.bottlenecks = _build_bottlenecks(selected, references)
    data.divisions = _build_divisions(selected, references)
    data.tasks = _build_tasks(selected, references)
    return data


def _build_totals(
    selected: Sequence[tuple[Mapping[str, Any], dict[str, Any], date | None]],
) -> Totals:
    totals = Totals(feedbacks=len(selected))

    rating_sum = 0.0
    for row, analysis, _ in selected:
        rating = feedback_rating(row, analysis)
        if rating is not None:
            rating_sum += rating
            totals.rated_feedbacks += 1

        totals.positives += _safe_int(_pick(row, analysis, "total_positives")) or 0
        totals.negatives += _safe_int(_pick(row, analysis, "total_negatives")) or 0
        totals.questions += len(_as_list(analysis.get("questions")))
        totals.tasks += len(_as_list(analysis.get("task_plan")))
        totals.client_requests += len(_as_list(analysis.get("client_request")))

        if _safe_int(_pick(row, analysis, "tone")) == 1:
            totals.emotional += 1
        if _safe_int(_pick(row, analysis, "form")) == 1:
            totals.rude += 1
        if _safe_int(_pick(row, analysis, "style")) == 1:
            totals.informal += 1

        for _, rating_value in _iter_quality_ratings(analysis):
            totals.quality_mentions += 1
            if _is_problem(rating_value):
                totals.problem_mentions += 1

    if totals.rated_feedbacks:
        totals.avg_rating = rating_sum / totals.rated_feedbacks
    return totals


def _build_moods(
    selected: Sequence[tuple[Mapping[str, Any], dict[str, Any], date | None]],
    references: ReferenceData,
) -> list[MoodBucket]:
    counts: dict[int, int] = defaultdict(int)
    for row, analysis, _ in selected:
        mood_id = _safe_int(_pick(row, analysis, "mood"))
        if mood_id:
            counts[mood_id] += 1

    total = sum(counts.values())
    if not total:
        return []

    return [
        MoodBucket(
            mood_id=mood_id,
            name=references.mood_name(mood_id),
            amount=counts.get(mood_id, 0),
            share=counts.get(mood_id, 0) / total,
        )
        for mood_id in sorted(set(counts) | {1, 2, 3})
    ]


def _choose_granularity(moments: Sequence[date]) -> str:
    if not moments:
        return "month"
    months = {(moment.year, moment.month) for moment in moments}
    if len(months) <= 1:
        return "day"
    if len({moment.year for moment in moments}) > 3:
        return "year"
    return "month"


def _build_timeline(
    selected: Sequence[tuple[Mapping[str, Any], dict[str, Any], date | None]],
    granularity: str,
) -> list[TimelinePoint]:
    points: dict[str, TimelinePoint] = {}

    for row, analysis, moment in selected:
        if moment is None:
            continue

        key, label = _period_key(moment, granularity)
        point = points.get(key)
        if point is None:
            point = TimelinePoint(key=key, label=label)
            points[key] = point

        point.feedbacks += 1
        point.positives += _safe_int(_pick(row, analysis, "total_positives")) or 0
        point.negatives += _safe_int(_pick(row, analysis, "total_negatives")) or 0

        rating = feedback_rating(row, analysis)
        if rating is not None:
            point.rating_sum += rating
            point.rating_count += 1

        for _, rating_value in _iter_quality_ratings(analysis):
            if _is_problem(rating_value):
                point.problem_mentions += 1

    return [points[key] for key in sorted(points)]


def _period_key(moment: date, granularity: str) -> tuple[str, str]:
    if granularity == "day":
        return moment.strftime("%Y-%m-%d"), moment.strftime("%d.%m")
    if granularity == "year":
        return moment.strftime("%Y"), moment.strftime("%Y")
    return (
        moment.strftime("%Y-%m"),
        f"{MONTH_LABELS[moment.month - 1]} {moment.year}",
    )


def _build_bottlenecks(
    selected: Sequence[tuple[Mapping[str, Any], dict[str, Any], date | None]],
    references: ReferenceData,
) -> list[Bottleneck]:
    items: dict[str, Bottleneck] = {}

    for _, analysis, _ in selected:
        for item, rating in _iter_quality_ratings(analysis):
            item_id = _safe_int(item.get("id"))
            reference = references.quality.get(item_id) if item_id else None

            if reference is not None:
                key = f"q{reference.id}"
                name = reference.name
                responsible = references.responsible_name(reference.responsible_id)
                known = True
            else:
                name = str(item.get("name") or "Без названия").strip()
                key = f"a:{name.lower()}"
                responsible = "Вне справочника"
                known = False

            bottleneck = items.get(key)
            if bottleneck is None:
                bottleneck = Bottleneck(
                    key=key,
                    name=name,
                    responsible=responsible,
                    known=known,
                )
                items[key] = bottleneck

            bottleneck.mentions += 1
            if rating is not None and rating != NO_RATING:
                bottleneck.rating_sum += rating
                bottleneck.rating_count += 1
            if _is_problem(rating):
                bottleneck.problems += 1

        for task in _as_list(analysis.get("task_plan")):
            item_id = _safe_int(task.get("id"))
            reference = references.quality.get(item_id) if item_id else None
            key = f"q{reference.id}" if reference else None
            if key and key in items:
                items[key].tasks += 1

    ranked = sorted(
        items.values(),
        key=lambda item: (
            -item.problems,
            -item.problem_share,
            item.avg_rating if item.avg_rating is not None else MAX_RATING,
            -item.mentions,
        ),
    )
    return ranked


def _build_divisions(
    selected: Sequence[tuple[Mapping[str, Any], dict[str, Any], date | None]],
    references: ReferenceData,
) -> list[DivisionStat]:
    stats: dict[int, DivisionStat] = {
        responsible.id: DivisionStat(
            id=responsible.id,
            name=responsible.name,
            fio=responsible.fio,
        )
        for responsible in references.responsibles.values()
    }

    def bucket(responsible_id: int | None) -> DivisionStat | None:
        if responsible_id is None:
            return None
        found = stats.get(responsible_id)
        if found is None:
            found = DivisionStat(
                id=responsible_id,
                name=references.responsible_name(responsible_id),
            )
            stats[responsible_id] = found
        return found

    for row, analysis, _ in selected:
        assigned = bucket(_safe_int(row["responsible_id"]))
        if assigned is not None:
            assigned.assigned_feedbacks += 1

        for item, rating in _iter_quality_ratings(analysis):
            reference = references.quality.get(_safe_int(item.get("id")) or -1)
            if reference is None:
                continue
            target = bucket(reference.responsible_id)
            if target is None:
                continue

            target.mentions += 1
            if rating is not None and rating != NO_RATING:
                target.rating_sum += rating
                target.rating_count += 1
            if _is_problem(rating):
                target.problems += 1

        for task in _as_list(analysis.get("task_plan")):
            service = references.services.get(_safe_int(task.get("id")) or -1)
            quality = references.quality.get(_safe_int(task.get("id")) or -1)
            responsible_id = (
                service.responsible_id if service else
                quality.responsible_id if quality else None
            )
            target = bucket(responsible_id)
            if target is not None:
                target.tasks += 1

    ranked = sorted(
        stats.values(),
        key=lambda item: (
            item.avg_rating if item.avg_rating is not None else -1.0,
            -item.problem_share,
            item.name,
        ),
        reverse=True,
    )
    return ranked


def _build_tasks(
    selected: Sequence[tuple[Mapping[str, Any], dict[str, Any], date | None]],
    references: ReferenceData,
) -> list[TaskStat]:
    counts: dict[tuple[str, str], int] = defaultdict(int)

    for _, analysis, _ in selected:
        for task in _as_list(analysis.get("task_plan")):
            item_id = _safe_int(task.get("id"))
            service = references.services.get(item_id or -1)
            if service is not None:
                name = service.name
                responsible = references.responsible_name(service.responsible_id)
            else:
                name = str(task.get("name") or "Без классификации").strip()
                responsible = "Вне справочника"
            counts[(name, responsible)] += 1

        for request in _as_list(analysis.get("client_request")):
            text = str(request.get("task") or "").strip()
            if text:
                counts[("Запрос от клиента", "Служба по работе с клиентами")] += 1

    return [
        TaskStat(name=name, responsible=responsible, amount=amount)
        for (name, responsible), amount in sorted(
            counts.items(), key=lambda pair: (-pair[1], pair[0][0])
        )
    ]


# ---------------------------------------------------------------------------
# Период
# ---------------------------------------------------------------------------

PERIOD_PRESETS = (
    ("all", "Весь период"),
    ("30", "Последние 30 дней"),
    ("90", "Последние 90 дней"),
    ("year", "Последний год"),
    ("custom", "Свой период"),
)


def resolve_period(
    preset: str,
    date_from: date | None,
    date_to: date | None,
    anchor: date | None,
) -> tuple[date | None, date | None]:
    """
    Возвращает границы периода.

    Относительные пресеты считаются от даты последнего отзыва в базе, а не от
    текущего дня: выгруженные отзывы часто относятся к прошедшим периодам.
    """
    if preset == "custom":
        return date_from, date_to
    if preset in ("30", "90") and anchor is not None:
        return anchor - timedelta(days=int(preset) - 1), anchor
    if preset == "year" and anchor is not None:
        return anchor - timedelta(days=364), anchor
    return None, None


def parse_feedback_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in DATE_FORMATS:
        try:
            return _strptime_date(text, pattern)
        except ValueError:
            continue
    return None


def _strptime_date(text: str, pattern: str) -> date:
    from datetime import datetime

    return datetime.strptime(text, pattern).date()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _iter_quality_ratings(
    analysis: Mapping[str, Any],
) -> Iterable[tuple[Mapping[str, Any], int | None]]:
    """Перебирает оценки критериев качества, включая внесправочные."""
    for key in ("quality_items", "adv_quality_items"):
        for item in _as_list(analysis.get(key)):
            yield item, _safe_int(item.get("rating"))


def _is_problem(rating: int | None) -> bool:
    return rating is not None and NO_RATING < rating <= PROBLEM_RATING_MAX


def feedback_rating(
    row: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> float | None:
    """
    Итоговая оценка отзыва.

    Модель заполняет total_rating не всегда (в реальных выгрузках там 0),
    поэтому при отсутствии значения оценка считается как среднее по
    выставленным оценкам критериев качества.
    """
    total = _safe_int(_pick(row, analysis, "total_rating"))
    if total is not None and total > NO_RATING:
        return float(total)

    ratings = [
        rating
        for _, rating in _iter_quality_ratings(analysis)
        if rating is not None and rating > NO_RATING
    ]
    return sum(ratings) / len(ratings) if ratings else None


def _pick(row: Mapping[str, Any], analysis: Mapping[str, Any], name: str) -> Any:
    """Берёт показатель из колонки таблицы, а при её пустоте — из JSON анализа."""
    try:
        value = row[name]
    except (KeyError, IndexError):
        value = None
    return analysis.get(name) if value is None else value


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _load_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None

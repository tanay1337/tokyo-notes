from .api import APIWidget
from .base import WidgetBase
from .habits import HabitTrackerWidget
from .rss import RSSWidget
from .tasks import TasksWidget
from .weather import WeatherWidget
from .worldtime import WorldTimeWidget

_registry: dict[str, type[WidgetBase]] = {}


def register(widget_class: type[WidgetBase]) -> None:
    _registry[widget_class.widget_type] = widget_class


def get_widget_types() -> dict[str, type[WidgetBase]]:
    return dict(_registry)


def create_widget(
    widget_type: str, widget_id: str, settings: dict | None = None, app=None
) -> WidgetBase:
    cls = _registry[widget_type]
    return cls(widget_id=widget_id, settings=settings, app=app)


register(TasksWidget)
register(RSSWidget)
register(APIWidget)
register(WeatherWidget)
register(WorldTimeWidget)
register(HabitTrackerWidget)

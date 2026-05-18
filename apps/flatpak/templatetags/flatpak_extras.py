from django import template
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def timesince_days(value):
    """Like |timesince but drops any component smaller than a day (hours, minutes).

    Examples:
      "37 days, 2 hours"   → "37 days"
      "2 months, 3 days"   → "2 months, 3 days"
      "2 months, 3 weeks"  → "2 months, 3 weeks"
      "5 hours, 20 minutes"→ "less than a day"
    """
    if not value:
        return ''
    result = timesince(value)
    # timesince returns up to two comma-separated components
    parts = [p.strip() for p in result.split(',')]
    _sub_day = ('hour', 'minute', 'second')
    filtered = [p for p in parts if not any(unit in p for unit in _sub_day)]
    if not filtered:
        return 'less than a day'
    return ', '.join(filtered)

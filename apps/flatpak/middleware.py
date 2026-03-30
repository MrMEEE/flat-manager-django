import zoneinfo
from django.utils import timezone


class TimezoneMiddleware:
    """Activate the timezone configured in SiteConfig for every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            from apps.flatpak.models import SiteConfig
            tz_name = SiteConfig.get_solo().timezone
            if tz_name:
                timezone.activate(zoneinfo.ZoneInfo(tz_name))
            else:
                timezone.deactivate()
        except Exception:
            timezone.deactivate()
        return self.get_response(request)

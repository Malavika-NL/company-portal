import os
import sys

from django.apps import AppConfig
from django.conf import settings


class IdentityConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'identity'

    def ready(self):
        """Warm all locally configured CRMs whenever the portal dev server starts."""
        if not getattr(settings, 'PORTAL_AUTO_START_CRMS_ON_BOOT', False):
            return
        if 'runserver' not in sys.argv:
            return

        # With Django's reloader, only the child process owns application work.
        # The portal's Vite supervisor uses --noreload, so it starts immediately.
        if '--noreload' not in sys.argv and os.environ.get('RUN_MAIN') != 'true':
            return

        from .local_services import warm_application_services

        warm_application_services()

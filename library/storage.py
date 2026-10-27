import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """File storage outside any web-served directory.

    Files are only reachable through views that check publication status or permissions.
    The root is read from settings on every access so tests/deployments can change it. To move
    to cloud storage later, swap this class (see DEPLOYMENT.md) - the models do not change.
    """

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None


def get_private_storage():
    return PrivateStorage()

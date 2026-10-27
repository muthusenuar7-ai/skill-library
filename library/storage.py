import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage


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


class BlobPrivateStorage(Storage):
    """Private Vercel objects, opened only by permission-checked application views."""

    @property
    def api(self):
        from vercel.blob import sync
        return sync

    def _open(self, name, mode="rb"):
        if mode not in ("r", "rb"):
            raise ValueError("Use Storage.save() to write private objects.")
        from vercel.blob import BlobNotFoundError
        try:
            return ContentFile(self.api.get_blob(name, access="private").body, name=name)
        except BlobNotFoundError as exc:
            raise FileNotFoundError(name) from exc

    def _save(self, name, content):
        result = self.api.put_blob(name, content.read(), access="private", add_random_suffix=False)
        return result.pathname

    def exists(self, name):
        from vercel.blob import BlobNotFoundError
        try:
            self.api.head_blob(name)
            return True
        except BlobNotFoundError:
            return False

    def delete(self, name):
        self.api.delete_blob(name)

    def size(self, name):
        return self.api.head_blob(name).size

    def listdir(self, path):
        prefix = path.rstrip("/") + "/" if path else ""
        directories, files = set(), []
        for item in self.api.query_blobs(prefix=prefix):
            relative = item.pathname[len(prefix):]
            if "/" in relative:
                directories.add(relative.split("/", 1)[0])
            else:
                files.append(relative)
        return sorted(directories), files


def get_private_storage():
    return BlobPrivateStorage() if settings.PRIVATE_MEDIA_STORAGE == "vercel_blob" else PrivateStorage()

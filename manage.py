#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    # Local command-line use defaults to development; wsgi/asgi default to production.
    os.environ.setdefault("DJANGO_ENV", "production" if os.environ.get("VERCEL") else "development")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

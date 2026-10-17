"""WSGI entry point: ``gunicorn --config ../scripts/linuxone/gunicorn.conf.py wsgi:app``."""

from silence_engine.app import create_app

app = create_app()

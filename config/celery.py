"""
Celery 앱 설정.
config/__init__.py에서 import하여 Django 시작 시 자동 로드.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")

# 등록된 모든 Django 앱에서 tasks.py를 자동 탐색
app.autodiscover_tasks()

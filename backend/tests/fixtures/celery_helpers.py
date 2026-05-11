"""Configure Celery for synchronous, in-process execution during tests.

Setting ``task_always_eager=True`` makes ``.delay()`` and ``.apply_async()`` run
the task body inline, so we don't need a worker process or a real Redis broker
for most tests.  Tests that explicitly need a broker can use the
``test-redis`` service from ``docker-compose.test.yml`` and a fakeredis fixture.
"""

from __future__ import annotations


def configure_celery_eager() -> None:
    from app.celery_app import celery_app

    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        broker_url="memory://",
        result_backend="cache+memory://",
    )

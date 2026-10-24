"""Кнопка «Стоп»: исключение и сон, который просыпается на «Стоп»."""
import threading
import time


class Stopped(Exception):
    """Нажата кнопка «Стоп»."""


def wait(seconds: float, stop: threading.Event) -> None:
    """Спать, но просыпаться на «Стоп» не позже чем через 0,2 с."""
    end = time.monotonic() + max(0.0, seconds)
    while True:
        if stop.is_set():
            raise Stopped()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.2, left))

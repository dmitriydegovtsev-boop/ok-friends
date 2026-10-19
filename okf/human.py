"""Поведение под человека: кривая мыши, промахи, прокрутка кусками, паузы с разбросом."""
import math
import random
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


def jitter(seconds: float, spread: float = 0.5) -> float:
    """Значение с разбросом ±spread (по умолчанию ±50 %)."""
    return seconds * random.uniform(1 - spread, 1 + spread)


class Mouse:
    """Плавное движение мыши к цели по кривой Безье со случайными отклонениями."""

    def __init__(self, page, stop: threading.Event):
        self.page = page
        self.stop = stop
        self.pos = (random.randint(200, 700), random.randint(150, 500))
        try:
            page.mouse.move(*self.pos)
        except Exception:
            pass

    @staticmethod
    def _bezier(p0, p1, p2, p3, t):
        u = 1 - t
        x = u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]
        y = u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]
        return x, y

    def _glide(self, x: float, y: float) -> None:
        sx, sy = self.pos
        dist = math.hypot(x - sx, y - sy)
        if dist < 1:
            return
        # контрольные точки: отклонение до четверти длины пути в случайную сторону
        bend = dist * random.uniform(0.08, 0.25)
        ang = math.atan2(y - sy, x - sx)
        side = random.choice((-1, 1))
        c1 = (sx + (x - sx) * 0.3 + math.cos(ang + side * math.pi / 2) * bend * random.uniform(0.5, 1),
              sy + (y - sy) * 0.3 + math.sin(ang + side * math.pi / 2) * bend * random.uniform(0.5, 1))
        c2 = (sx + (x - sx) * 0.7 + math.cos(ang - side * math.pi / 2) * bend * random.uniform(0, 0.6),
              sy + (y - sy) * 0.7 + math.sin(ang - side * math.pi / 2) * bend * random.uniform(0, 0.6))
        steps = max(10, int(dist / random.uniform(6, 14)))
        duration = 0.25 + (dist / 900.0) * random.uniform(0.5, 1.4)
        for i in range(1, steps + 1):
            if self.stop.is_set():
                raise Stopped()
            t = i / steps
            # ускорение в начале и замедление в конце
            e = t * t * (3 - 2 * t)
            px, py = self._bezier((sx, sy), c1, c2, (x, y), e)
            px += random.uniform(-0.8, 0.8)
            py += random.uniform(-0.8, 0.8)
            self.page.mouse.move(px, py)
            time.sleep(duration / steps * random.uniform(0.5, 1.6))
        self.page.mouse.move(x, y)
        self.pos = (x, y)

    def move_to(self, x: float, y: float) -> None:
        dist = math.hypot(x - self.pos[0], y - self.pos[1])
        # иногда промах на пару пикселей и поправка
        if dist > 120 and random.random() < 0.3:
            ox = x + random.choice((-1, 1)) * random.uniform(3, 9)
            oy = y + random.choice((-1, 1)) * random.uniform(2, 7)
            self._glide(ox, oy)
            time.sleep(random.uniform(0.05, 0.2))
        self._glide(x, y)

    def point_in(self, box: dict) -> tuple:
        """Случайная точка в центральных 60 % элемента."""
        x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
        y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
        return x, y

    def hover(self, locator) -> dict:
        locator.scroll_into_view_if_needed(timeout=5000)
        time.sleep(random.uniform(0.1, 0.4))
        box = locator.bounding_box()
        if not box:
            raise LookupError("элемент без координат")
        self.move_to(*self.point_in(box))
        return box

    def click(self, locator) -> None:
        self.hover(locator)
        time.sleep(random.uniform(0.06, 0.25))
        self.page.mouse.down()
        time.sleep(random.uniform(0.04, 0.13))
        self.page.mouse.up()

    def scroll(self, total: int) -> None:
        """Прокрутка кусками по 150–450 px с паузами; total > 0 вниз, < 0 вверх."""
        left = abs(total)
        sign = 1 if total > 0 else -1
        while left > 0:
            if self.stop.is_set():
                raise Stopped()
            chunk = min(left, random.randint(150, 450))
            self.page.mouse.wheel(0, sign * chunk)
            left -= chunk
            time.sleep(random.uniform(0.12, 0.6))
        time.sleep(random.uniform(0.2, 0.7))

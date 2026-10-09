"""Dependency-free color sampling and playback timeline primitives."""

import math

RGB = tuple[int, int, int]


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def colors_for_lights(
    left: RGB, right: RGB, xs: list[float], brightness: float = 0.7
) -> list[RGB]:
    """Map light coordinates in [-1, 1] to brightness-scaled RGB colors.

    Positions below -0.05 select left; above 0.05 select right; the closed
    center interval selects the channel-wise mean. Brightness is in [0, 1].
    Channels are rounded with Python's round (ties to even).
    """
    for name, rgb in (("left", left), ("right", right)):
        if not isinstance(rgb, (tuple, list)):
            raise TypeError(f"{name} must be an RGB sequence")
        if len(rgb) != 3:
            raise ValueError(f"{name} must have three channels")
        if any(type(c) is not int for c in rgb):
            raise TypeError(f"{name} channels must be integers")
        if any(not 0 <= c <= 255 for c in rgb):
            raise ValueError(f"{name} channels must be in [0, 255]")
    if not isinstance(xs, list):
        raise TypeError("xs must be a list")
    _finite_number(brightness, "brightness")
    if not 0 <= brightness <= 1:
        raise ValueError("brightness must be in [0, 1]")
    for x in xs:
        _finite_number(x, "x")
        if not -1 <= x <= 1:
            raise ValueError("x must be in [-1, 1]")
    center = tuple((a + b) / 2 for a, b in zip(left, right))
    return [tuple(round(c * brightness) for c in
                  (left if x < -0.05 else right if x > 0.05 else center))
            for x in xs]


class PlaybackClock:
    """Estimate playback seconds from Jellyfin session snapshots.

    Supply finite monotonic seconds (normally time.monotonic) to both public
    methods. Equal timestamps are allowed. Identical item/tick/pause reports
    never refresh the anchor. Playing estimates expire strictly after 20 s;
    paused estimates stay frozen indefinitely. ``active`` means an item is
    present, even when its estimate is stale.

    A tick decrease relative to the previous report is a backward seek.
    Otherwise delayed reports and pause/resume cannot rewind the estimate.
    An item change starts an independent timeline. Snapshots are not retained.
    """

    def __init__(self):
        self._item_id = None
        self._paused = False
        self._anchor_position = 0.0
        self._anchor_time = 0.0
        self._last_report = None
        self._last_now = None

    @property
    def item_id(self) -> str | None:
        return self._item_id

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active(self) -> bool:
        return self._item_id is not None

    def _check_time(self, now: float):
        _finite_number(now, "now")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("now must be monotonic across updates and position reads")

    def update(self, session: dict | None, now: float) -> None:
        self._check_time(now)
        if session is not None and not isinstance(session, dict):
            raise TypeError("session must be a dict or None")
        item = None if session is None else session.get("NowPlayingItem")
        if item is not None and not isinstance(item, dict):
            raise TypeError("NowPlayingItem must be a dict or None")
        item_id = None if item is None else item.get("Id")
        if item_id is not None and not isinstance(item_id, str):
            raise TypeError("NowPlayingItem.Id must be a string")
        if not item_id:
            self._last_now = now
            self._item_id = None
            self._paused = False
            self._anchor_position = 0.0
            self._anchor_time = now
            self._last_report = None
            return
        state = session.get("PlayState")
        if state is None:
            raise ValueError("Playing sessions must include PlayState")
        if not isinstance(state, dict):
            raise TypeError("PlayState must be a dict")
        ticks = state.get("PositionTicks")
        if ticks is None:
            raise ValueError("Playing sessions must include PositionTicks")
        if type(ticks) is not int:
            raise TypeError("PositionTicks must be an integer")
        if ticks < 0:
            raise ValueError("PositionTicks must be nonnegative")
        paused = state.get("IsPaused", False)
        if type(paused) is not bool:
            raise TypeError("IsPaused must be a boolean")
        self._last_now = now
        if (item_id, paused, ticks) == (self._item_id, self._paused, self._last_report):
            return
        reported_position = ticks / 10_000_000
        if item_id == self._item_id and ticks >= self._last_report:
            reported_position = max(reported_position, self._estimate(now))
        self._item_id = item_id
        self._paused = paused
        self._last_report = ticks
        self._anchor_position = reported_position
        self._anchor_time = now

    def _estimate(self, now: float) -> float:
        elapsed = 0 if self.paused else min(now - self._anchor_time, 20)
        return self._anchor_position + elapsed

    def position(self, now: float) -> float | None:
        self._check_time(now)
        self._last_now = now
        if not self.active:
            return None
        if not self.paused and now - self._anchor_time > 20:
            return None
        return self._estimate(now)


def sample_rgb(data: bytes, width: int, height: int) -> tuple[RGB, RGB]:
    """Average RGB24 halves after trimming entirely near-black borders.

    Near-black means all channels are <= 12. Each edge can lose at most
    floor(original dimension / 3) pixels. Rows are trimmed before columns.
    The odd middle column belongs to the right half. A single remaining
    column supplies both halves. Means use round (ties to even).
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be packed RGB24 bytes")
    if type(width) is not int or type(height) is not int:
        raise TypeError("width and height must be integers")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(data) != width * height * 3:
        raise ValueError("RGB24 data length must equal width * height * 3")
    x0, x1, y0, y1 = 0, width, 0, height

    def black(x, y):
        offset = (y * width + x) * 3
        return max(data[offset:offset + 3]) <= 12

    while y0 < height // 3 and all(black(x, y0) for x in range(width)):
        y0 += 1
    while height - y1 < height // 3 and all(black(x, y1 - 1) for x in range(width)):
        y1 -= 1
    while x0 < width // 3 and all(black(x0, y) for y in range(y0, y1)):
        x0 += 1
    while width - x1 < width // 3 and all(black(x1 - 1, y) for y in range(y0, y1)):
        x1 -= 1
    split = x0 + (x1 - x0) // 2

    def average(start, stop):
        red = green = blue = 0
        count = (stop - start) * (y1 - y0)
        for y in range(y0, y1):
            for x in range(start, stop):
                offset = (y * width + x) * 3
                red += data[offset]
                green += data[offset + 1]
                blue += data[offset + 2]
        return round(red / count), round(green / count), round(blue / count)

    return average(x0, max(x0 + 1, split)), average(split, x1)

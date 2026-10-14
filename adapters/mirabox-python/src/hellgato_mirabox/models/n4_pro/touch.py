"""Translate N4 touch samples and completion events to CORA commands."""

class TouchGesture:
    def __init__(self):
        self.first = None
        self.last = None
        self.started = 0
        self.updated = 0

    def point(self, x, y, now):
        if not (0 <= x < 800 and 0 <= y < 112):
            return
        point = (x, y * 100 // 112)
        if self.first is None or now - self.updated > 0.25:
            self.first = point
            self.started = now
        self.last = point
        self.updated = now

    def finish(self, now, direction=None):
        fresh = self.first is not None and now - self.updated <= 0.25
        command = None
        if direction in ('left', 'right'):
            if fresh and abs(self.last[0] - self.first[0]) >= 20:
                start, end = self.first, self.last
            else:
                # Captures show SDK "right" for decreasing physical X.
                start, end = ((700, 50), (100, 50)) if direction == 'right' else ((100, 50), (700, 50))
            command = f'touch swipe {start[0]} {start[1]} {end[0]} {end[1]}'
        elif fresh:
            kind = 'hold' if now - self.started >= 0.5 else 'tap'
            command = f'touch {kind} {self.first[0]} {self.first[1]}'
        self.first = self.last = None
        return command

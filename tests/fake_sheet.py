"""An in-memory stand-in for the Google Sheets client used by the sync tests."""


class FakeClient:
    def __init__(self, rows=None, tabs=("Tracker",)):
        self.rows = [list(r) for r in (rows or [])]
        self.tabs = list(tabs)
        self.shared: list[tuple[str, str]] = []
        self.writes = 0

    def create(self, title, tab="Tracker"):
        return "sid1", "https://docs.google.com/spreadsheets/d/sid1"

    def share(self, file_id, email):
        self.shared.append((file_id, email))

    def info(self, sheet_id):
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}", self.tabs

    def read(self, sheet_id, rng):
        return [list(r) for r in self.rows]

    def write(self, sheet_id, rng, values):
        self.rows = [list(r) for r in values]
        self.writes += 1

    def clear(self, sheet_id, rng):
        self.rows = self.rows[:1]

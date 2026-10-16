class RedirectException(Exception):
    """Raised to force a redirect from deep inside a dependency."""
    def __init__(self, url: str):
        self.url = url
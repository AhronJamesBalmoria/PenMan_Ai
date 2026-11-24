import sys

import uvicorn

# Windows consoles default to cp1252. Model output often contains characters
# (em dashes, curly quotes, emoji) that cannot be encoded there, which makes
# logging handlers raise UnicodeEncodeError and swallow the line entirely.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )

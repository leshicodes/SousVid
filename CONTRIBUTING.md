# Contributing to SousVid

This document captures the code standards and conventions for this project.
Follow them when adding features or fixing bugs so the codebase stays consistent.

---

## Python version

Python 3.12+. Use union syntax (`X | None`, `list[str]`) rather than `Optional[X]`
or `List[str]`.

---

## Project layout

```
app/
  config.py          -- all env var reading; no os.getenv() elsewhere
  errors.py          -- typed exception hierarchy
  models.py          -- Pydantic request/response models
  main.py            -- FastAPI app, routing, startup/shutdown
  pipeline.py        -- orchestration only; no I/O logic here
  downloader.py      -- yt-dlp wrapper
  frame_extractor.py -- ffmpeg wrapper
  transcriber.py     -- faster-whisper wrapper
  llm.py             -- OpenRouter / OpenAI SDK wrapper
  mealie.py          -- Mealie API client
tests/
  test_pipeline_heuristic.py
  test_mealie.py
  test_health.py
static/
  index.html         -- single-file frontend
```

---

## Environment variables

**All** environment variables are declared in `app/config.py` as fields on the
`Settings` class. No module should call `os.getenv()` directly -- import
`settings` from `app.config` instead.

```python
# good
from app.config import settings
url = settings.mealie_base_url

# bad
import os
url = os.getenv("MEALIE_URL", "").rstrip("/")
```

When adding a new env var:
1. Add it to `app/config.py` with a type, default, and docstring.
2. Add it to `.env.example` with a comment explaining what it does.
3. Update the README setup section if it's user-facing.

---

## Error handling

Use the typed exceptions from `app/errors.py` for known failure modes:

| Exception | When to raise |
|---|---|
| `DownloadError` | yt-dlp can't download the video |
| `TranscriptionError` | Whisper fails unexpectedly |
| `ExtractionError` | LLM returns non-parseable output |
| `SousVidError` | catch-all for anything else pipeline-specific |

`main.py` maps these to appropriate HTTP status codes. Don't raise raw `RuntimeError`
or `ValueError` from business logic -- the API layer won't know how to categorize them.

Mealie upload errors are **never** re-raised. The `post_to_mealie()` function swallows
them intentionally and returns `{"skipped": True, "reason": "..."}`.

---

## Imports

Order: stdlib → third-party → internal. One blank line between each group.

```python
import json
import logging

import httpx
from pydantic import BaseModel

from app.config import settings
from app.errors import DownloadError
```

---

## Logging

- `logger = logging.getLogger(__name__)` at module level, always.
- No `print()` statements.
- No `logging.basicConfig()` outside `main.py`.
- Log *why* something happened, not just *what*:
  ```python
  # good
  logger.info("Caption looks like a full recipe -- skipping Whisper.")
  # not useful
  logger.info("Skipping Whisper.")
  ```

---

## Docstrings

Every module and every public function gets a docstring. Keep them honest:

- **Module docstrings:** one-line summary, then a brief explanation of the design
  decision or constraint that a reader needs to know.
- **Function docstrings:** what it does, what it returns, what it raises. Skip
  Args/Returns sections for trivial functions.
- Don't restate the code. If the docstring says the same thing as the signature,
  delete it.

---

## Style and formatting

`ruff` handles both linting and formatting. Run before committing:

```bash
pip install -r requirements-dev.txt
ruff check app/ tests/
ruff format app/ tests/
```

Key rules:
- 88-character line length
- Double quotes for strings
- 4-space indent, no tabs

---

## Tests

Run with:

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

The test suite covers pure-function unit tests and the `/health` endpoint.
Integration tests (real Mealie, real video URLs) are out of scope -- keep tests
fast and self-contained.

When adding a new feature, add at minimum:
- A test for the happy path
- A test for the main failure mode

---

## Magic values

Name your constants. If a number or string has meaning, give it a name at the
top of the module:

```python
# good
JPEG_QUALITY = 3
FRAME_WIDTH = 640

# bad
"-q:v", "3",
"-vf", "scale=640:-2",
```

---

## Dependencies

- `requirements.txt` -- runtime deps, all pinned except `yt-dlp` (see comment in file).
- `requirements-dev.txt` -- dev tools, not installed in the Docker image.
- Don't add a new dependency without a clear reason. Check if something already
  in the dependency tree covers the need (`httpx` is already present; don't add another HTTP client).
- `requests` must stay even though our code uses `httpx` -- `faster-whisper` imports
  it internally for HuggingFace model downloads. Removing it breaks startup.

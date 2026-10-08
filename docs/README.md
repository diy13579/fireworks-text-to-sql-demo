# Browser demo

This folder contains the Fireworks-only GitHub Pages demonstration.

Generate the sanitized question set and schema context:

```bash
python scripts/build_demo_assets.py
```

Serve the repository root so the page can load `data/financial.sqlite`:

```bash
python -m http.server 8000
```

Then open:

```text
http://localhost:8000/docs/
```

The GitHub Pages workflow publishes the contents of this folder at the site root
and stages the public SQLite database under `data/`.

No Fireworks key is committed or provided by GitHub Actions. Live users paste
their own key, which remains in the browser tab and is sent directly to the
Fireworks API.

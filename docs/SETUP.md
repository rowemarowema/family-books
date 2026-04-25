# Family Books — environment setup

System dependencies that aren't `pip install -e .[dev]`-able.

## Windows local development (WeasyPrint / GTK)

The trial-balance PDF export uses [WeasyPrint](https://weasyprint.org/),
which renders HTML to PDF via Pango (text shaping) and Cairo (vector
graphics). On Windows these come from the GTK runtime.

Install the **GTK3 runtime for Windows** from
<https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases>
(the `gtk3-runtime-*-win64.exe` installer). Restart the shell so the
new DLLs are on `PATH`. Then:

```bash
make install      # picks up weasyprint
python -c "import weasyprint; print(weasyprint.__version__)"
```

If the import fails on Windows, it's almost always GTK. The PDF
exporter degrades gracefully — the URL `?format=pdf` returns HTTP 503
with a clear message, while CSV/XLSX/HTML continue to work. The
bundled `pytest.mark.skipif(...)` on the PDF smoke test additionally
handles the "developer skipped GTK" case so the suite stays green
locally.

WSL2 + Linux distro is also a valid path — the apt-install steps in
the production section below apply there.

## Production deploy on Render

Render's standard Python runtime is Debian-based but doesn't include
the GTK system libraries by default. Add them to the build command in
`render.yaml`:

```yaml
services:
  - type: web
    name: family-books
    runtime: python
    buildCommand: |
      apt-get update && apt-get install -y \
        libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf2.0-0 && \
      pip install --upgrade pip && \
      pip install .
    startCommand: gunicorn family_books.wsgi:application
```

Notes:

- Use `pip install .` (non-editable) for production, not
  `pip install -e .` (editable mode is a developer convenience).
- The apt-install line is the only Family-Books-specific build step;
  Render handles `pyproject.toml` discovery automatically once it sees
  a Python project.
- After the first successful deploy, eyeball that `?format=pdf`
  produces a real PDF (Content-Type, file size, opening it). If it
  503s, the apt-install was silently skipped or the package names
  drifted between Debian releases.
- A planned Group H follow-up is a startup check that exercises
  `import weasyprint` once at boot and fails fast (rather than at
  first PDF request) if the system libs are missing — defense in depth
  on top of the runtime 503 fallback.

## Optional: Postgres + age locally

Documented elsewhere — see `Makefile` (`make db-up` / `make db-down`)
and the recovery + rollback playbooks. WeasyPrint is the only system
dep introduced by Group F.5.

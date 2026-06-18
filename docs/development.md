# Development Notes

## Project Layout

- `main.py` is the thin runtime entrypoint.
- `main.py` starts the Python HTTP backend and opens the Vue frontend in a `pywebview` desktop window.
- `main.py --server-only` starts only the Python HTTP backend.
- `src/fh6auto/backend/` contains the FastAPI app, backend composition root, runtime service, configuration service, and HTTP state model.
- `frontend/` contains the Vue 3 + Vite frontend.
- `src/fh6auto/backend/app.py` owns the `BackendApp` composition root, which holds `AppServices`, `AppFlows`, and `RuntimeState`.
- `src/fh6auto/backend/composition.py` groups concrete service and flow instances without adding inheritance.
- `src/fh6auto/automation/`, `vision/`, `input/`, and `flows/` contain the concrete service and workflow implementations.
- `src/fh6auto/bootstrap.py` handles process setup before native UI imports.
- `src/fh6auto/paths.py` owns resource and config paths.
- `src/fh6auto/config.py` owns the current config schema and JSON persistence.
- `src/fh6auto/input/` owns input backends and low-level Windows input structures.

## Dependency Management

Use `uv` for dependency changes:

```powershell
uv add <package>
uv sync
```

Use npm inside `frontend/` for Vue dependencies:

```powershell
cd frontend
npm install
npm run dev
```

During frontend development, Vite proxies `/api` to `http://127.0.0.1:8000`.

## Local Checks

Run these before committing Python changes:

```powershell
uv run ruff check src main.py
uv run python -m compileall src main.py
git diff --check
```

Run the frontend production build when frontend files change:

```powershell
cd frontend
npm run build
cd ..
```

## UML Class Diagram

Install development dependencies first:

```powershell
uv sync --group dev
```

Generate the final UML class diagram after architecture changes have stabilized:

```powershell
uv run pyreverse -o pdf -p FH6Auto -d docs src/fh6auto
Remove-Item -LiteralPath docs/packages_FH6Auto.pdf -ErrorAction SilentlyContinue
```

The class diagram is written to `docs/classes_FH6Auto.pdf`. `pyreverse` also emits a package diagram by default; remove `docs/packages_FH6Auto.pdf` unless it is intentionally needed.

## Version Management

The only version source is `[project].version` in `pyproject.toml`. Use `uv version` to read or update it:

```powershell
uv version --short
uv version 1.2.3
uv version --bump patch
```

Do not hard-code release versions in package modules.

## Configuration

`DEFAULT_CONFIG` in `src/fh6auto/config.py` is the current schema. Unknown keys in `config.json` are dropped when the app loads and saves the file. The project intentionally does not migrate historical config names.

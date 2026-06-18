# SAM custom build (Metadata.BuildMethod: makefile). Delegates to build.py so
# the recipe is identical on Windows, macOS and Linux -- the previous POSIX
# `mkdir -p`/`cp` recipe failed under Windows cmd.exe, and its CWD-relative
# `../../../migrations` path could not resolve from SAM's scratch build dir.
#
# build.py bundles app.py, the repo-root migrations/ tree (located via
# ARTIFACTS_DIR), and the Alembic engine; psycopg v3 comes from the shared
# bdo-common layer at runtime.
#
# Run via `uv run` so the build does not depend on a `python` binary on PATH
# (many systems only ship `python3`/`uv`). `--no-project` keeps it independent
# of the repo venv (SAM runs this from a scratch copy); `--with pip` guarantees
# pip, which build.py invokes via `sys.executable -m pip`.
build-MigratorFunction:
	uv run --no-project --with pip python build.py "$(ARTIFACTS_DIR)"

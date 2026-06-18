# SAM custom build (Metadata.BuildMethod: makefile). Delegates to build.py so
# the recipe is identical on Windows, macOS and Linux (see migrator/Makefile for
# the rationale behind avoiding a POSIX mkdir/cp recipe).
#
# build.py bundles app.py and the repo-root infra/openapi.yaml (located via
# ARTIFACTS_DIR), and vendors PyYAML for the Lambda runtime target.
#
# Run via `uv run` so the build does not depend on a `python` binary on PATH
# (many systems only ship `python3`/`uv`). `--no-project` keeps it independent
# of the repo venv (SAM runs this from a scratch copy); `--with pip` guarantees
# pip, which build.py invokes via `sys.executable -m pip`.
build-DocsFunction:
	uv run --no-project --with pip python build.py "$(ARTIFACTS_DIR)"

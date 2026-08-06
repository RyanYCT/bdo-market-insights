# SAM custom build (Metadata.BuildMethod: makefile). Delegates to build.py so
# the recipe is identical on Windows, macOS and Linux. build.py bundles app.py
# and the committed tracked-set data files (scripts/data/*.json, located via
# ARTIFACTS_DIR); bdo_common (tracking/dynamo) + Powertools come from the shared
# layer at runtime, so nothing is pip-installed here.
#
# Run via `uv run` so the build does not depend on a `python` binary on PATH
# (many systems only ship `python3`/`uv`). `--no-project` keeps it independent
# of the repo venv (SAM runs this from a scratch copy).
build-SeedTrackedFunction:
	uv run --no-project python build.py "$(ARTIFACTS_DIR)"

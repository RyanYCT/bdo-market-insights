# SAM custom build (Metadata.BuildMethod: makefile) for the bdo-common layer.
# Delegates to build_layer.py so the recipe is identical on Windows, macOS and
# Linux. The previous `BuildMethod: python3.12` installed only requirements.txt
# and dropped the hand-authored bdo_common package, so every function failed at
# init with "No module named 'bdo_common'". build_layer.py places both
# bdo_common and the pip deps under python/ (the layer import root, /opt/python).
#
# Run via `uv run` (the project standardises on uv) so the build does not depend
# on a `python` binary being on PATH -- many systems only ship `python3`/`uv`.
# `--no-project` keeps it independent of the repo venv (SAM runs this from a
# scratch copy), and `--with pip` guarantees the interpreter has pip, which
# build_layer.py invokes via `sys.executable -m pip` to vendor the runtime deps.
build-CommonLayer:
	uv run --no-project --with pip python build_layer.py "$(ARTIFACTS_DIR)"

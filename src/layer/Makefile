# SAM custom build (Metadata.BuildMethod: makefile) for the bdo-common layer.
# Delegates to build_layer.py so the recipe is identical on Windows, macOS and
# Linux. The previous `BuildMethod: python3.12` installed only requirements.txt
# and dropped the hand-authored bdo_common package, so every function failed at
# init with "No module named 'bdo_common'". build_layer.py places both
# bdo_common and the pip deps under python/ (the layer import root, /opt/python).
build-CommonLayer:
	python build_layer.py "$(ARTIFACTS_DIR)"

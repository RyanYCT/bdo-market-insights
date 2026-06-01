# SAM custom build (Metadata.BuildMethod: makefile). Bundles the
# single-source-of-truth migrations/ tree from the repo root alongside the
# handler and installs the Alembic engine. psycopg v3 comes from the shared
# bdo-common layer at runtime, so it is intentionally not reinstalled here.
#
# SAM runs this target with CWD set to this directory (the function CodeUri)
# and ARTIFACTS_DIR pointing at the build output.
build-MigratorFunction:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp app.py "$(ARTIFACTS_DIR)/"
	cp -R ../../../migrations "$(ARTIFACTS_DIR)/migrations"
	python -m pip install -r requirements.txt -t "$(ARTIFACTS_DIR)"

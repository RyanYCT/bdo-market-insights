# SAM custom build (Metadata.BuildMethod: makefile). Delegates to build.py so
# the recipe is identical on Windows, macOS and Linux (see migrator/Makefile for
# the rationale behind avoiding a POSIX mkdir/cp recipe).
#
# build.py bundles app.py and the repo-root infra/openapi.yaml (located via
# ARTIFACTS_DIR), and vendors PyYAML for the Lambda runtime target.
build-DocsFunction:
	python build.py "$(ARTIFACTS_DIR)"

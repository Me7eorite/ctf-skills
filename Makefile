.PHONY: ui-dev ui-build ui-test ui-lint ui-typecheck ui-bundle-size

ui-dev:
	cd frontend && npm run dev

ui-build:
	cd frontend && npm run build

ui-test:
	cd frontend && npm run test

ui-lint:
	cd frontend && npm run lint

ui-typecheck:
	cd frontend && npm run typecheck

# Logs the gzipped size of the initial JS bundle (the entry chunk emitted by
# Vite as src/web/static/dist/assets/index-*.js). Warns above 800 KB gzipped.
ui-bundle-size:
	@python3 scripts/bundle_size_check.py

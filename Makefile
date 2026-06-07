.PHONY: dev watch install clean help

help:
	@echo "Faugus Launcher - Available targets:"
	@echo "  dev       - Run locally without installing (python3 -m faugus.launcher)"
	@echo "  watch     - Watch source files and rebuild on changes"
	@echo "  install   - Build and install to system (meson setup + ninja + ninja install)"
	@echo "  clean     - Remove build directory"

dev:
	python3 -m faugus.launcher

watch:
	find faugus -name "*.py" | entr -r make dev

install:
	meson setup builddir --prefix=/usr/local
	cd builddir && ninja
	cd builddir && sudo ninja install

clean:
	rm -rf builddir

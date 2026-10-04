.PHONY: dev watch install rpm rpm-build clean help

PROJECT      := faugus-launcher
VERSION      := $(shell sed -n "s/^ *version: *'\([^']*\)'.*/\1/p" meson.build | head -1)
RPMBUILD_DIR := $(CURDIR)/.rpmbuild
TARBALL      := $(PROJECT)-$(VERSION).tar.gz
RPM_TOPDIR   := $(RPMBUILD_DIR)/BUILD/$(PROJECT)-$(VERSION)

# Resolved inside the recipe, not at parse time, so it reflects this build.
rpm_file = $(firstword $(wildcard $(RPMBUILD_DIR)/RPMS/noarch/*.rpm))

help:
	@echo "Faugus Launcher - Available targets:"
	@echo "  dev        - Run locally without installing (python3 -m faugus.launcher)"
	@echo "  watch      - Watch source files and rebuild on changes"
	@echo "  install    - Build and install to system (meson setup + ninja + ninja install)"
	@echo "  rpm        - Build the RPM from the current tree and install it"
	@echo "  rpm-build  - Build the RPM without installing it"
	@echo "  clean      - Remove build directories"
	@echo ""
	@echo "  Version: $(VERSION)"

dev:
	python3 -m faugus.launcher

watch:
	uvx watchfiles "/usr/bin/python3 -m faugus.launcher" faugus

install:
	meson setup builddir --prefix=/usr/local
	cd builddir && ninja
	cd builddir && sudo ninja install

# Build a noarch RPM from the working tree and install it.
#
# meson.build is the single source of truth for the version. The tracked spec
# still carries upstream's own (stale) Version, and its Source0 points at a
# GitHub tarball that doesn't match this tree, so a spec is generated into
# .rpmbuild/SPECS for the build only. packaging/fedora/faugus-launcher.spec is
# never modified.
rpm: rpm-build
	@if [ -z "$(rpm_file)" ]; then echo "No RPM produced in $(RPMBUILD_DIR)/RPMS/noarch/" >&2; exit 1; fi
	@if [ -e /usr/local/bin/$(PROJECT) ] || [ -d /usr/local/lib/python3*/site-packages/$(PROJECT) ]; then \
		echo "WARNING: a previous 'make install' copy exists under /usr/local and may"; \
		echo "         shadow this RPM. Remove it with: sudo make purge-local"; \
	fi
	sudo dnf install -y "$(rpm_file)"

rpm-build:
	@test -n "$(VERSION)" || { echo "Could not read version from meson.build" >&2; exit 1; }
	rm -rf "$(RPM_TOPDIR)"
	mkdir -p "$(RPMBUILD_DIR)"/{SOURCES,SPECS,RPMS,SRPMS,BUILD,build}
	git archive --format=tar --prefix=$(PROJECT)-$(VERSION)/ HEAD | gzip -9 > "$(RPMBUILD_DIR)/SOURCES/$(TARBALL)"
	sed -e 's|^Version:.*|Version:        $(VERSION)|' \
	    -e 's|^Release:.*|Release:        1%{?dist}.gse|' \
	    -e 's|^Source0:.*|Source0:        $(TARBALL)|' \
	    packaging/fedora/$(PROJECT).spec > "$(RPMBUILD_DIR)/SPECS/$(PROJECT).spec"
	rpmbuild --define "_topdir $(RPMBUILD_DIR)" \
	         --define "_build_id_links none" \
	         --define "source_date_epoch_from_changelog %{nil}" \
	         -ba "$(RPMBUILD_DIR)/SPECS/$(PROJECT).spec"
	@echo ""
	@ls -1 $(RPMBUILD_DIR)/RPMS/noarch/*.rpm

# Remove leftovers from 'make install' (prefix=/usr/local). Only touches code
# installed under /usr/local; user data lives in ~/.local/share/faugus-launcher,
# ~/.config/faugus-launcher, ~/.local/state/faugus-launcher and ~/Faugus and is
# never affected. Everything here is also provided by the RPM under /usr, so
# nothing is lost.
purge-local:
	sudo rm -f /usr/local/bin/faugus-*
	sudo rm -rf /usr/local/lib/python3*/site-packages/$(PROJECT)
	sudo rm -rf /usr/local/share/$(PROJECT) /usr/local/share/licenses/$(PROJECT)
	sudo rm -f /usr/local/share/applications/faugus-*.desktop \
	           /usr/local/share/applications/io.github.Faugus.*.desktop \
	           /usr/local/share/metainfo/*.metainfo.xml
	sudo rm -f /usr/local/share/icons/hicolor/scalable/apps/faugus-*.svg \
	           /usr/local/share/icons/hicolor/scalable/apps/io.github.Faugus.*.svg \
	           /usr/local/share/icons/hicolor/scalable/actions/faugus-*.svg
	sudo rm -f /usr/local/share/locale/*/LC_MESSAGES/faugus-*.mo
	-sudo gtk-update-icon-cache -f -t /usr/local/share/icons/hicolor
	@echo "Remaining faugus files under /usr/local (should be none):"
	@find /usr/local -maxdepth 5 -iname "*faugus*" 2>/dev/null || true
	rm -rf builddir

clean:
	rm -rf builddir .rpmbuild
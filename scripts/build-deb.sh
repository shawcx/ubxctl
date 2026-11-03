#!/bin/sh
# Build dist/ubxctl_<version>_all.deb from the working tree.
#
# Needs: sudo apt install debhelper dh-python python3-textual python3-serial python3-pytest
#        (lintian too, to check the result)
#
# Builds in a temporary copy of the tracked and untracked (not ignored) files, so uncommitted
# edits are included and the checkout stays clean. pyubx2, pynmeagps and pyrtcm aren't in Debian:
# their wheels are downloaded from PyPI, checked against debian/vendor.sha256 and unpacked into
# debian/vendor. Off a release tag, the version gets a +git<date>.<commit> snapshot suffix.
set -eu
cd "$(dirname "$0")/.."

ver=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
deb_ver=$(dpkg-parsechangelog -S Version)
case "$deb_ver" in
    "$ver"-*) ;;
    *) echo "debian/changelog is at $deb_ver but pyproject.toml is at $ver" >&2; exit 1 ;;
esac
if [ "$(git describe --tags --exact-match HEAD 2>/dev/null || true)" != "v$ver" ]; then
    deb_ver="$ver+git$(git log -1 --format=%cd --date=format:%Y%m%d).$(git rev-parse --short HEAD)-${deb_ver##*-}"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
src="$tmp/ubxctl-$ver"
mkdir "$src"
git ls-files -z --cached --others --exclude-standard \
    | tar --null --ignore-failed-read -T - -cf - | tar -xf - -C "$src"
sed -i "1s/([^)]*)/($deb_ver)/" "$src/debian/changelog"

mkdir "$tmp/wheels" "$src/debian/vendor"
while read -r sha url; do
    whl="$tmp/wheels/${url##*/}"
    curl -sSfL -o "$whl" "$url"
    echo "$sha  $whl" | sha256sum -c --quiet
    python3 -I -m zipfile -e "$whl" "$tmp/wheels/x"
done < debian/vendor.sha256
for pkg in pyubx2 pynmeagps pyrtcm; do
    cp -r "$tmp/wheels/x/$pkg" "$src/debian/vendor/"
done

(cd "$src" && dpkg-buildpackage -b -us -uc)

if command -v lintian >/dev/null; then
    lintian --info "$tmp"/ubxctl_*.changes || true
fi
mkdir -p dist
deb="ubxctl_${deb_ver}_all.deb"
mv "$tmp/$deb" dist/
echo "Built dist/$deb"

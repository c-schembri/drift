#!/bin/sh
set -eu

repository="${DRIFT_REPOSITORY:-c-schembri/drift}"
version="${DRIFT_VERSION:-latest}"
case "$version" in *[!A-Za-z0-9._-]*) echo "drift: invalid version" >&2; exit 1 ;; esac
system=$(uname -s)
machine=$(uname -m)
case "$system" in
  Linux) platform=linux ;;
  Darwin) platform=macos ;;
  *) echo "drift: unsupported system: $system" >&2; exit 1 ;;
esac
case "$machine" in
  x86_64|amd64) architecture=x86_64 ;;
  arm64|aarch64) architecture=arm64 ;;
  *) echo "drift: unsupported architecture: $machine" >&2; exit 1 ;;
esac

asset="drift-$platform-$architecture.tar.gz"
release="https://github.com/$repository/releases"
base="$release/latest/download"
[ "$version" = latest ] || base="$release/download/v$version"
home="${DRIFT_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/drift}"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT INT TERM

curl --fail --location --silent --show-error "$base/$asset" --output "$temporary/$asset"
curl --fail --location --silent --show-error "$base/SHA256SUMS" --output "$temporary/SHA256SUMS"
expected=$(awk -v name="$asset" '$2 == name { print $1 }' "$temporary/SHA256SUMS")
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$temporary/$asset" | awk '{ print $1 }')
else
  actual=$(shasum -a 256 "$temporary/$asset" | awk '{ print $1 }')
fi
[ -n "$expected" ] && [ "$expected" = "$actual" ] || { echo "drift: release checksum mismatch" >&2; exit 1; }

destination="$home/versions/${version}-$(printf %.12s "$actual")"
mkdir -p "$home/versions" "$home/bin"
if [ ! -d "$destination" ]; then
  mkdir "$destination"
  tar -xzf "$temporary/$asset" -C "$destination"
fi
chmod +x "$destination/drift"
ln -sfn "$destination/drift" "$home/bin/drift"
printf 'Installed Drift at %s\nAdd %s to PATH.\n' "$destination" "$home/bin"

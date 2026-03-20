#!/bin/sh
set -eu

REPO_SLUG="${PERPBOT_REPO_SLUG:-morfize/perp-bot}"
VERSION="${PERPBOT_VERSION:-latest}"
INSTALL_DIR="${PERPBOT_INSTALL_DIR:-$HOME/.local/bin}"
TMP_DIR="${TMPDIR:-/tmp}"

print_help() {
    cat <<EOF
Install the latest prebuilt perpbot binary as a normal shell command.

Usage:
  sh scripts/install.sh
  curl -fsSL https://raw.githubusercontent.com/$REPO_SLUG/main/scripts/install.sh | sh

Environment:
  PERPBOT_VERSION       Release tag to install, for example v0.1.3. Default: latest
  PERPBOT_INSTALL_DIR   Install location. Default: \$HOME/.local/bin
  PERPBOT_REPO_SLUG     GitHub repo slug. Default: morfize/perp-bot
  PERPBOT_OS            Override detected OS for testing.
  PERPBOT_ARCH          Override detected architecture for testing.

Options:
  --print-asset         Print the release asset name and exit.
  --print-url           Print the release download URL and exit.
  --print-install-dir   Print the resolved install directory and exit.
  -h, --help            Show this help text.
EOF
}

detect_os() {
    os="${PERPBOT_OS:-$(uname -s)}"
    case "$os" in
        Linux)
            printf '%s\n' "linux"
            ;;
        Darwin)
            printf '%s\n' "macos"
            ;;
        *)
            printf 'Unsupported operating system: %s\n' "$os" >&2
            exit 1
            ;;
    esac
}

detect_arch() {
    arch="${PERPBOT_ARCH:-$(uname -m)}"
    case "$arch" in
        x86_64|amd64)
            printf '%s\n' "x86_64"
            ;;
        arm64|aarch64)
            printf '%s\n' "arm64"
            ;;
        *)
            printf 'Unsupported architecture: %s\n' "$arch" >&2
            exit 1
            ;;
    esac
}

asset_name() {
    os="$(detect_os)"
    arch="$(detect_arch)"
    printf 'perpbot-%s-%s.tar.gz\n' "$os" "$arch"
}

download_url() {
    asset="$(asset_name)"
    if [ "$VERSION" = "latest" ]; then
        printf 'https://github.com/%s/releases/latest/download/%s\n' "$REPO_SLUG" "$asset"
        return 0
    fi

    printf 'https://github.com/%s/releases/download/%s/%s\n' "$REPO_SLUG" "$VERSION" "$asset"
}

download_file() {
    url="$1"
    output="$2"

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$output"
        return 0
    fi

    if command -v wget >/dev/null 2>&1; then
        wget -qO "$output" "$url"
        return 0
    fi

    printf '%s\n' "Neither curl nor wget is available to download $url" >&2
    exit 1
}

case "${1:-}" in
    --print-asset)
        asset_name
        exit 0
        ;;
    --print-url)
        download_url
        exit 0
        ;;
    --print-install-dir)
        printf '%s\n' "$INSTALL_DIR"
        exit 0
        ;;
    -h|--help)
        print_help
        exit 0
        ;;
    "")
        ;;
    *)
        printf 'Unknown argument: %s\n' "$1" >&2
        print_help >&2
        exit 1
        ;;
esac

asset="$(asset_name)"
url="$(download_url)"
archive="$TMP_DIR/$asset.$$"
extract_dir="$TMP_DIR/perpbot-install.$$"

cleanup() {
    rm -f "$archive"
    rm -rf "$extract_dir"
}

trap cleanup EXIT HUP INT TERM

mkdir -p "$INSTALL_DIR"
mkdir -p "$extract_dir"

printf 'Downloading %s\n' "$url" >&2
download_file "$url" "$archive"
tar -xzf "$archive" -C "$extract_dir"
install -m 755 "$extract_dir/perpbot" "$INSTALL_DIR/perpbot"

cat <<EOF
perpbot installed to $INSTALL_DIR/perpbot

Next:
  perpbot --help

If your shell cannot find \`perpbot\` yet, add this to PATH:
  export PATH="$INSTALL_DIR:\$PATH"
EOF

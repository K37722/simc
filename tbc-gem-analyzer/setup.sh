#!/usr/bin/env bash
# Sets up the WoWSims TBC (Anniversary) simulator used by the gem analyzer:
# clones wowsims/tbc-new, generates the Go protobuf code and builds the
# wowsimcli binary (with the item database embedded).
#
# Requirements: git, go >= 1.24, protoc (protobuf-compiler package).
set -euo pipefail

SIM_REPO="${TBC_SIM_REPO:-$HOME/wowsims/tbc-new}"

if ! command -v go >/dev/null; then
    echo "error: go toolchain not found (need go >= 1.24)" >&2; exit 1
fi
if ! command -v protoc >/dev/null; then
    echo "error: protoc not found. Install it, e.g.: sudo apt-get install protobuf-compiler" >&2
    exit 1
fi

if [ ! -d "$SIM_REPO/.git" ]; then
    echo "Cloning wowsims/tbc-new into $SIM_REPO ..."
    git clone --depth 1 https://github.com/wowsims/tbc-new "$SIM_REPO"
else
    echo "Updating existing checkout at $SIM_REPO ..."
    git -C "$SIM_REPO" pull --ff-only || echo "  (pull failed; using existing checkout)"
fi

cd "$SIM_REPO"

if ! command -v protoc-gen-go >/dev/null && [ ! -x "$(go env GOPATH)/bin/protoc-gen-go" ]; then
    echo "Installing protoc-gen-go ..."
    go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
fi
export PATH="$PATH:$(go env GOPATH)/bin"

echo "Generating protobuf code ..."
protoc -I=./proto \
    --go_opt=Mgoogle/protobuf/descriptor.proto=google.golang.org/protobuf/types/descriptorpb \
    --go_out=./sim/core ./proto/*.proto

echo "Building wowsimcli (this can take a few minutes) ..."
go build -tags with_db -o wowsimcli-bin ./cmd/wowsimcli

echo
echo "Done. Simulator built at: $SIM_REPO/wowsimcli-bin"
echo "Run the analyzer with:"
echo "  python3 analyze.py --import <your_export.json> --sim-repo \"$SIM_REPO\""

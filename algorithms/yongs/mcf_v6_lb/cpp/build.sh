#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
clang++ -std=c++17 -O3 -fPIC -shared \
  -I/Library/gurobi1301/macos_universal2/include \
  "$SCRIPT_DIR/ocam_v6_lb_cpp.cpp" \
  /Library/gurobi1301/macos_universal2/lib/libgurobi_c++.a \
  -L/Library/gurobi1301/macos_universal2/lib \
  -Wl,-rpath,/Library/gurobi1301/macos_universal2/lib \
  -lgurobi130 \
  -o "$SCRIPT_DIR/libocam_v6_lb_cpp.so"

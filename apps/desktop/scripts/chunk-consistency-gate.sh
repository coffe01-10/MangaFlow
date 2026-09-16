#!/usr/bin/env bash
# Sourceable helper: the referenced-chunks consistency gate (#734 residue).
#
# The static export's index.html references content-hashed chunks under
# /_next/static/; an internally inconsistent export (index.html referencing
# chunks that were never written) shipped a data-less shell that only a
# full browser run caught (#734). This gate fails the build instead.
#
# SOURCEd (no side effects at source time) — exercised by
# test_build_frontend_static_gate.py, which run-sidecar-e2e.sh collects.
#
# check_referenced_chunks <frontend_dir> <index_html>
#   Exit 0 when every /_next/static/... reference the entry document makes
#   exists as a file under <frontend_dir>; exit 1 and list every missing
#   reference on stderr otherwise. Zero references passes (nothing to
#   check — the /_next/static gate is a subset contract, the entry/route
#   smoke gate covers the rest).
check_referenced_chunks() {
  local frontend_dir="$1" index_html="$2"
  if [ ! -f "$index_html" ]; then
    echo "chunk gate: index.html not found at $index_html" >&2
    return 1
  fi
  local ref missing=0
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    if [ ! -f "$frontend_dir$ref" ]; then
      echo "smoke gate: index.html references missing chunk: $ref" >&2
      missing=1
    fi
  done < <(grep -oE '/_next/static/[A-Za-z0-9/_.-]+' "$index_html" | sort -u)
  return "$missing"
}

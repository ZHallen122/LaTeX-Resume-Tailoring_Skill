#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./setup.sh [--host claude|codex|both] [--upgrade] [--force] [--version]

Install the latex-resume-tailoring skill for Claude Code and/or Codex.

Options:
  --host    Target host(s). Default: both.
              claude -> ${CLAUDE_HOME:-$HOME/.claude}/skills/latex-resume-tailoring
              codex  -> ${CODEX_HOME:-$HOME/.codex}/skills/latex-resume-tailoring
  --upgrade Replace an installed copy only when this checkout is newer.
  --force   Replace an installed copy regardless of version.
  --version Print the source skill version and exit.
  -h, --help
EOF
}

read_version() {
  local version_file="$1"
  if [[ -f "$version_file" ]]; then
    local version
    version="$(<"$version_file")"
    version="${version%%$'\n'*}"
    version="${version//$'\r'/}"
    version="${version//[[:space:]]/}"
    [[ -n "$version" ]] && { printf '%s\n' "$version"; return 0; }
  fi
  printf 'unknown\n'
}

validate_version() {
  local version="${1#v}"
  [[ "$version" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]
}

compare_versions() {
  local left="${1#v}" right="${2#v}"
  local lM lm lp rM rm rp
  IFS=. read -r lM lm lp <<<"$left"
  IFS=. read -r rM rm rp <<<"$right"
  lm="${lm:-0}"; lp="${lp:-0}"; rm="${rm:-0}"; rp="${rp:-0}"
  if   (( 10#$lM > 10#$rM )); then printf '1\n'
  elif (( 10#$lM < 10#$rM )); then printf -- '-1\n'
  elif (( 10#$lm > 10#$rm )); then printf '1\n'
  elif (( 10#$lm < 10#$rm )); then printf -- '-1\n'
  elif (( 10#$lp > 10#$rp )); then printf '1\n'
  elif (( 10#$lp < 10#$rp )); then printf -- '-1\n'
  else printf '0\n'; fi
}

install_to() {
  local skills_dir="$1" host_label="$2"
  local target_dir="$skills_dir/$skill_name"

  if [[ -e "$target_dir" ]]; then
    local installed_version
    installed_version="$(read_version "$target_dir/VERSION")"

    if [[ "$force" -ne 1 && "$upgrade" -ne 1 ]]; then
      if [[ "$installed_version" == "$source_version" ]]; then
        echo "[$host_label] already installed at $target_dir (v$source_version); no changes."
        return 0
      fi
      echo "[$host_label] already installed at $target_dir (installed: $installed_version, source: $source_version)." >&2
      echo "[$host_label] rerun with --upgrade or --force." >&2
      return 1
    fi

    if [[ "$upgrade" -eq 1 ]]; then
      if [[ "$installed_version" == "unknown" ]] || ! validate_version "$installed_version"; then
        echo "[$host_label] installed copy at $target_dir has no valid VERSION; rerun with --force." >&2
        return 1
      fi
      local cmp
      cmp="$(compare_versions "$source_version" "$installed_version")"
      if [[ "$cmp" -eq 0 ]]; then
        echo "[$host_label] already at v$source_version; no changes."
        return 0
      elif [[ "$cmp" -lt 0 ]]; then
        echo "[$host_label] refusing to downgrade v$installed_version -> v$source_version; use --force." >&2
        return 1
      fi
    fi
    # Refuse to delete a directory that doesn't look like an installed copy of
    # this skill — protects against a misconfigured CLAUDE_HOME/CODEX_HOME
    # pointing the path at an unrelated directory.
    if [[ ! -f "$target_dir/SKILL.md" && ! -f "$target_dir/VERSION" ]]; then
      echo "[$host_label] $target_dir exists but does not look like this skill (no SKILL.md/VERSION); refusing to delete. Remove it manually." >&2
      return 1
    fi
    rm -rf "$target_dir"
  fi

  mkdir -p "$skills_dir"
  local tmp_dir
  tmp_dir="$(mktemp -d "$skills_dir/.${skill_name}.tmp.XXXXXX")"
  trap 'rm -rf "$tmp_dir"' RETURN
  cp -R "$source_dir/." "$tmp_dir/"
  mv "$tmp_dir" "$target_dir"
  trap - RETURN
  echo "[$host_label] installed v$source_version -> $target_dir"
}

host="both"
force=0
upgrade=0
show_version=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="${2:-}"; shift 2 ;;
    --upgrade) upgrade=1; shift ;;
    --force) force=1; shift ;;
    --version) show_version=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$host" in
  claude|codex|both) ;;
  *) echo "Invalid --host '$host' (expected claude, codex, or both)" >&2; exit 2 ;;
esac

if [[ "$force" -eq 1 && "$upgrade" -eq 1 ]]; then
  echo "Use either --upgrade or --force, not both." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_name="latex-resume-tailoring"
source_dir="$repo_root/$skill_name"
source_version="$(read_version "$source_dir/VERSION")"

if [[ "$show_version" -eq 1 ]]; then
  printf '%s\n' "$source_version"
  exit 0
fi

if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "Missing $source_dir/SKILL.md. Run this script from the repository root." >&2
  exit 1
fi
if [[ "$source_version" == "unknown" ]] || ! validate_version "$source_version"; then
  echo "Missing or invalid $source_dir/VERSION (need numeric semver such as 0.2.0)." >&2
  exit 1
fi
if ! grep -q "^name: $skill_name$" "$source_dir/SKILL.md"; then
  echo "Skill metadata does not declare name: $skill_name" >&2
  exit 1
fi

status=0
if [[ "$host" == "claude" || "$host" == "both" ]]; then
  install_to "${CLAUDE_HOME:-$HOME/.claude}/skills" "claude" || status=1
fi
if [[ "$host" == "codex" || "$host" == "both" ]]; then
  install_to "${CODEX_HOME:-$HOME/.codex}/skills" "codex" || status=1
fi

if [[ "$status" -eq 0 ]]; then
  cat <<'EOF'

Invoke it from Claude Code:
  Use the latex-resume-tailoring skill to tailor my resume.tex for this JD. Generate strict and stretch variants.
Or from Codex:
  Use $latex-resume-tailoring to tailor my resume.tex for this JD. Generate strict and stretch variants.

Restart the host if it was already running.
EOF
fi
exit "$status"

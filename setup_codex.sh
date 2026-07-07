#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./setup_codex.sh [--upgrade] [--force] [--version]

Install the latex-resume-tailoring skill and bundled review cockpit for Codex.

Options:
  --upgrade Replace the installed skill only when this copy has a newer version.
  --force   Replace an existing installed skill directory.
  --version Show the source skill version and exit.
  -h, --help
            Show this help message.

Install location:
  ${CODEX_HOME:-$HOME/.codex}/skills/latex-resume-tailoring

Bundled review cockpit:
  ${CODEX_HOME:-$HOME/.codex}/skills/latex-resume-tailoring/resume-review-ui/index.html
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
    if [[ -n "$version" ]]; then
      printf '%s\n' "$version"
      return 0
    fi
  fi
  printf 'unknown\n'
}

validate_version() {
  local version="${1#v}"
  [[ "$version" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]
}

compare_versions() {
  local left="${1#v}"
  local right="${2#v}"
  local left_major left_minor left_patch right_major right_minor right_patch

  IFS=. read -r left_major left_minor left_patch <<<"$left"
  IFS=. read -r right_major right_minor right_patch <<<"$right"

  left_minor="${left_minor:-0}"
  left_patch="${left_patch:-0}"
  right_minor="${right_minor:-0}"
  right_patch="${right_patch:-0}"

  if (( 10#$left_major > 10#$right_major )); then
    printf '1\n'
  elif (( 10#$left_major < 10#$right_major )); then
    printf -- '-1\n'
  elif (( 10#$left_minor > 10#$right_minor )); then
    printf '1\n'
  elif (( 10#$left_minor < 10#$right_minor )); then
    printf -- '-1\n'
  elif (( 10#$left_patch > 10#$right_patch )); then
    printf '1\n'
  elif (( 10#$left_patch < 10#$right_patch )); then
    printf -- '-1\n'
  else
    printf '0\n'
  fi
}

copy_review_ui() {
  local destination="$1"
  mkdir -p "$destination/$ui_name"
  cp -R "$ui_source_dir/." "$destination/$ui_name/"
}

force=0
upgrade=0
show_version=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --upgrade)
      upgrade=1
      shift
      ;;
    --force)
      force=1
      shift
      ;;
    --version)
      show_version=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_name="latex-resume-tailoring"
ui_name="resume-review-ui"
source_dir="$repo_root/$skill_name"
ui_source_dir="$repo_root/$ui_name"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skills_dir="$codex_home/skills"
target_dir="$skills_dir/$skill_name"
target_ui_file="$target_dir/$ui_name/index.html"
source_version="$(read_version "$source_dir/VERSION")"

if [[ "$show_version" -eq 1 ]]; then
  printf '%s\n' "$source_version"
  exit 0
fi

if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "Missing $source_dir/SKILL.md. Run this script from the repository root." >&2
  exit 1
fi

if [[ ! -f "$ui_source_dir/index.html" || ! -f "$ui_source_dir/app.js" || ! -f "$ui_source_dir/styles.css" ]]; then
  echo "Missing bundled review cockpit files under $ui_source_dir." >&2
  exit 1
fi

if [[ "$source_version" == "unknown" ]]; then
  echo "Missing $source_dir/VERSION. Add a semantic version such as 0.1.0." >&2
  exit 1
fi

if ! validate_version "$source_version"; then
  echo "Invalid source version '$source_version' in $source_dir/VERSION. Use numeric semver such as 0.1.0." >&2
  exit 1
fi

if ! grep -q "^name: $skill_name$" "$source_dir/SKILL.md"; then
  echo "Skill metadata does not declare name: $skill_name" >&2
  exit 1
fi

if [[ "$force" -eq 1 && "$upgrade" -eq 1 ]]; then
  echo "Use either --upgrade or --force, not both." >&2
  exit 2
fi

mkdir -p "$skills_dir"

if [[ -e "$target_dir" ]]; then
  installed_version="$(read_version "$target_dir/VERSION")"

  if [[ "$force" -ne 1 && "$upgrade" -ne 1 ]]; then
    if [[ "$installed_version" == "$source_version" ]]; then
      if [[ ! -f "$target_ui_file" ]]; then
        copy_review_ui "$target_dir"
        cat <<EOF
Skill already installed:
  $target_dir

Version:
  $source_version

Added bundled review cockpit:
  $target_ui_file
EOF
        exit 0
      fi

      cat <<EOF
Skill already installed:
  $target_dir

Version:
  $source_version

Review cockpit:
  $target_ui_file

No changes made.
EOF
      exit 0
    fi

    cat >&2 <<EOF
Skill already installed at:
  $target_dir

Installed version:
  $installed_version

Source version:
  $source_version

Run with --upgrade to replace it only if the source version is newer.
Run with --force to replace it regardless of version.
EOF
    exit 1
  fi

  if [[ "$upgrade" -eq 1 ]]; then
    if [[ "$installed_version" == "unknown" ]]; then
      cat >&2 <<EOF
Installed skill has no VERSION file:
  $target_dir

Run with --force to replace it.
EOF
      exit 1
    fi

    if ! validate_version "$installed_version"; then
      cat >&2 <<EOF
Installed skill has an invalid version '$installed_version':
  $target_dir/VERSION

Run with --force to replace it.
EOF
      exit 1
    fi

    version_cmp="$(compare_versions "$source_version" "$installed_version")"
    if [[ "$version_cmp" -eq 0 ]]; then
      if [[ ! -f "$target_ui_file" ]]; then
        copy_review_ui "$target_dir"
        cat <<EOF
Skill already installed at the same version:
  $target_dir

Version:
  $source_version

Added bundled review cockpit:
  $target_ui_file
EOF
        exit 0
      fi

      cat <<EOF
Skill already installed at the same version:
  $target_dir

Version:
  $source_version

Review cockpit:
  $target_ui_file

No changes made.
EOF
      exit 0
    elif [[ "$version_cmp" -lt 0 ]]; then
      cat >&2 <<EOF
Refusing to downgrade installed skill:
  $target_dir

Installed version:
  $installed_version

Source version:
  $source_version

Run with --force if you intentionally want to replace the installed copy.
EOF
      exit 1
    fi
  fi

  rm -rf "$target_dir"
fi

tmp_dir="$(mktemp -d "$skills_dir/.${skill_name}.tmp.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

mkdir -p "$tmp_dir/$skill_name"
cp -R "$source_dir/." "$tmp_dir/$skill_name/"
copy_review_ui "$tmp_dir/$skill_name"
mv "$tmp_dir/$skill_name" "$target_dir"

cat <<EOF
Installed Codex skill:
  $target_dir

Version:
  $source_version

Bundled review cockpit:
  $target_ui_file

Restart Codex if it is already running, then invoke it with:
  Use \$latex-resume-tailoring to tailor my resume.tex for this JD using my career vault. Generate strict and stretch variants.
EOF

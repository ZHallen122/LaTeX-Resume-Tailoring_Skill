#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./setup_codex.sh [--force]

Install the latex-resume-tailoring skill for Codex only.

Options:
  --force   Replace an existing installed skill directory.
  -h, --help
            Show this help message.

Install location:
  ${CODEX_HOME:-$HOME/.codex}/skills/latex-resume-tailoring
EOF
}

force=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      force=1
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
source_dir="$repo_root/$skill_name"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skills_dir="$codex_home/skills"
target_dir="$skills_dir/$skill_name"

if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "Missing $source_dir/SKILL.md. Run this script from the repository root." >&2
  exit 1
fi

if ! grep -q "^name: $skill_name$" "$source_dir/SKILL.md"; then
  echo "Skill metadata does not declare name: $skill_name" >&2
  exit 1
fi

mkdir -p "$skills_dir"

if [[ -e "$target_dir" ]]; then
  if [[ "$force" -ne 1 ]]; then
    cat >&2 <<EOF
Skill already installed at:
  $target_dir

Run with --force to replace it.
EOF
    exit 1
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
mv "$tmp_dir/$skill_name" "$target_dir"

cat <<EOF
Installed Codex skill:
  $target_dir

Restart Codex if it is already running, then invoke it with:
  Use \$latex-resume-tailoring to tailor my resume.tex for this JD using my career vault.
EOF

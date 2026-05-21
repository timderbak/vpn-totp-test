#!/usr/bin/env bash
#
# install-plugins.sh — declaratively enable VibecoSwagaTemplate plugins in
# ~/.claude/settings.json. Plugins install globally (~/.claude/plugins/) and
# auto-update on `claude` startup.
#
# Usage: ./scripts/install-plugins.sh
#
# Requires: jq. Works on macOS default bash 3.2 (no associative arrays used).
#
# How it works:
#   1. Merges `enabledPlugins` entries into ~/.claude/settings.json
#      (backing up first). Claude Code reads this on startup and
#      auto-fetches missing plugins.
#   2. Prints a one-time list of marketplaces you need to register manually
#      via `/plugin marketplace add <source>` inside `claude` (there is no
#      scriptable CLI for marketplace registration as of this writing).

set -eu

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGINS_FILE="${REPO_ROOT}/.claude-plugins.json"
SETTINGS_FILE="${HOME}/.claude/settings.json"
BACKUP_FILE="${HOME}/.claude/settings.json.bak.$(date +%Y%m%d-%H%M%S)"
KNOWN_MARKETPLACES_FILE="${HOME}/.claude/plugins/known_marketplaces.json"

# --- lookup functions (bash 3.2 compatible — no associative arrays) ---

# Plugin → marketplace mapping. Default is claude-plugins-official.
plugin_marketplace() {
  case "$1" in
    claude-mem)      echo "thedotmack" ;;
    reflexion)       echo "context-engineering-kit" ;;
    context-mode)    echo "context-mode" ;;
    ui-ux-pro-max)   echo "claude-code-templates" ;;
    *)               echo "claude-plugins-official" ;;
  esac
}

# Plugins NOT distributed via Claude marketplaces — handled manually.
# Returns install hint, or empty if plugin is a regular marketplace plugin.
plugin_skip_hint() {
  case "$1" in
    gsd) echo "npx get-shit-done-cc --claude --global" ;;
    *)   echo "" ;;
  esac
}

# Marketplace → source (for `/plugin marketplace add <source>`).
# Returns empty for claude-plugins-official (preinstalled by default).
# Sources verified against a working ~/.claude/plugins/known_marketplaces.json.
marketplace_source() {
  case "$1" in
    thedotmack)              echo "thedotmack/claude-mem" ;;
    context-engineering-kit) echo "NeoLabHQ/context-engineering-kit" ;;
    context-mode)            echo "mksglu/context-mode" ;;
    claude-code-templates)   echo "davila7/claude-code-templates" ;;
    *)                       echo "" ;;
  esac
}

# --- sanity checks ---

if [ ! -f "${PLUGINS_FILE}" ]; then
  echo "error: ${PLUGINS_FILE} not found" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "error: 'jq' not installed. brew install jq" >&2
  exit 1
fi

mkdir -p "${HOME}/.claude"
[ -f "${SETTINGS_FILE}" ] || echo "{}" > "${SETTINGS_FILE}"

# --- collect plugins from .claude-plugins.json ---

required_plugins=""
optional_plugins=""
while IFS="$(printf '\t')" read -r name required; do
  if [ "${required}" = "true" ]; then
    required_plugins="${required_plugins}${name}
"
  else
    optional_plugins="${optional_plugins}${name}
"
  fi
done <<EOF
$(jq -r '.plugins[] | [.name, (.required // false | tostring)] | @tsv' "${PLUGINS_FILE}")
EOF

# --- build the enabledPlugins JSON patch ---

patch="{}"
echo "${required_plugins}" | while IFS= read -r plugin; do
  : # nothing — placeholder; we'll use a different approach below
done

# Subshell from `while` would lose `patch`. Use a for-loop instead.
old_ifs="$IFS"
IFS="
"
for plugin in $required_plugins; do
  [ -z "$plugin" ] && continue
  skip="$(plugin_skip_hint "$plugin")"
  [ -n "$skip" ] && continue
  marketplace="$(plugin_marketplace "$plugin")"
  key="${plugin}@${marketplace}"
  patch=$(jq -n --argjson p "${patch}" --arg k "${key}" '$p + {($k): true}')
done
IFS="$old_ifs"

# --- merge into settings.json ---

cp "${SETTINGS_FILE}" "${BACKUP_FILE}"
echo "==> Backed up settings to: ${BACKUP_FILE}"

jq --argjson patch "${patch}" \
   '.enabledPlugins = ((.enabledPlugins // {}) + $patch)' \
   "${SETTINGS_FILE}" > "${SETTINGS_FILE}.tmp"
mv "${SETTINGS_FILE}.tmp" "${SETTINGS_FILE}"

echo "==> Enabled in ${SETTINGS_FILE}:"
IFS="
"
for plugin in $required_plugins; do
  [ -z "$plugin" ] && continue
  skip="$(plugin_skip_hint "$plugin")"
  [ -n "$skip" ] && continue
  marketplace="$(plugin_marketplace "$plugin")"
  echo "    ✓ ${plugin}@${marketplace}"
done
IFS="$old_ifs"

# --- list marketplaces that still need a one-time manual registration ---

needed=""
IFS="
"
for plugin in $required_plugins; do
  [ -z "$plugin" ] && continue
  skip="$(plugin_skip_hint "$plugin")"
  [ -n "$skip" ] && continue
  marketplace="$(plugin_marketplace "$plugin")"
  source="$(marketplace_source "$marketplace")"
  [ -z "$source" ] && continue
  # skip if already registered
  if [ -f "${KNOWN_MARKETPLACES_FILE}" ] \
     && jq -e --arg m "${marketplace}" 'has($m)' "${KNOWN_MARKETPLACES_FILE}" >/dev/null 2>&1; then
    continue
  fi
  needed="${needed}${marketplace}|${source}
"
done
IFS="$old_ifs"

if [ -n "$needed" ]; then
  echo ""
  echo "==> One-time marketplace registration required."
  echo "    Open \`claude\` and run these commands once:"
  echo ""
  echo "$needed" | sort -u | while IFS='|' read -r mp src; do
    [ -z "$mp" ] && continue
    echo "    /plugin marketplace add ${src}"
  done
fi

# --- optional plugins (printed, not installed) ---

if [ -n "$optional_plugins" ]; then
  echo ""
  echo "==> Optional plugins (skipped; enable manually if you want them):"
  IFS="
"
  for plugin in $optional_plugins; do
    [ -z "$plugin" ] && continue
    marketplace="$(plugin_marketplace "$plugin")"
    echo "    /plugin install ${plugin}@${marketplace}"
  done
  IFS="$old_ifs"
fi

# --- skipped plugins (npm CLIs, etc.) ---

skipped_any=""
IFS="
"
for plugin in $required_plugins; do
  [ -z "$plugin" ] && continue
  skip="$(plugin_skip_hint "$plugin")"
  [ -z "$skip" ] && continue
  if [ -z "$skipped_any" ]; then
    echo ""
    echo "==> Install separately (not Claude Code marketplace plugins):"
    skipped_any="1"
  fi
  echo "    • ${plugin}: ${skip}"
done
IFS="$old_ifs"

echo ""
echo "==> Done. Now run \`claude\` once — it will auto-fetch enabled plugins."
echo "    Restore the previous settings with: mv ${BACKUP_FILE} ${SETTINGS_FILE}"

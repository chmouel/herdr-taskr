#!/bin/sh
# Link the CLI into ~/.local/bin so prompts and scripts can call it by name.
set -u
name=herdr-taskr
root=${HERDR_PLUGIN_ROOT:-$(cd "$(dirname "$0")" && pwd -P)}
target=$root/bin/$name
dir=$HOME/.local/bin
link=$dir/$name

chmod +x "$target" 2>/dev/null || true

[ -d "$dir" ] || exit 0
if [ -e "$link" ] && [ ! -L "$link" ]; then
  echo "$name: $link is not a symlink, leaving it alone" >&2
  exit 0
fi
current=$(readlink "$link" 2>/dev/null || true)
case $current in
  "$target") exit 0 ;;
  "" | */bin/$name) ;;
  *)
    echo "$name: $link points to $current, leaving it alone" >&2
    exit 0
    ;;
esac
ln -sfn "$target" "$link" && echo "$name: linked $link"
exit 0

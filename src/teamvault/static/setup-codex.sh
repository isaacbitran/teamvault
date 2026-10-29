#!/bin/sh
# One-command setup for Codex (CLI, app and IDE extension all read ~/.codex/config.toml + ~/.codex/AGENTS.md).
#   curl -fsSL <server>/setup-codex.sh -o /tmp/tv.sh && sh /tmp/tv.sh     (prompts for your token, hidden)
# Non-interactive: TEAMVAULT_TOKEN=tv_... sh /tmp/tv.sh. Set TEAMVAULT_NO_SCHEDULE=1 to skip the 15-minute refresh.
set -e
SERVER="${TEAMVAULT_URL:-__TEAMVAULT_URL__}"
CODEX="$HOME/.codex"; mkdir -p "$CODEX"
printf "Team token (from your teamvault admin): "
if [ -z "$TEAMVAULT_TOKEN" ] && [ -r /dev/tty ] && [ -t 1 ]; then
  stty -echo </dev/tty 2>/dev/null || true; read TOKEN </dev/tty; stty echo </dev/tty 2>/dev/null || true
else TOKEN="${TEAMVAULT_TOKEN:-$(head -n1)}"; fi
echo
case "$TOKEN" in tv_*) ;; *) echo "That doesn't look like a team token (starts with tv_)."; exit 1;; esac
TOKEN=$(printf '%s' "$TOKEN" | tr -d ' \r\n')
code=$(curl -s --max-time 15 -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" "$SERVER/briefing" || true)
case "$code" in
  200) ;;
  401) echo "The server rejected that token — check you pasted it fully."; exit 1;;
  000|"") echo "Can't reach $SERVER — check your internet and try again."; exit 1;;
  *) echo "Server error ($code) — try again in a minute."; exit 1;;
esac

# 1) MCP server in config.toml (literal header: apps launched from the Dock don't see shell env vars)
CFG="$CODEX/config.toml"; touch "$CFG"; chmod 600 "$CFG"
python3 - "$CFG" "$SERVER" "$TOKEN" <<'PY'
import re, sys
cfg, server, token = sys.argv[1:]
t = open(cfg).read()
t = re.sub(r"\n?\[mcp_servers\.team\][^\[]*", "\n", t)
t = t.rstrip() + f'''

[mcp_servers.team]
url = "{server}/mcp"
http_headers = {{ Authorization = "Bearer {token}" }}
startup_timeout_sec = 20
tool_timeout_sec = 120
'''
open(cfg, "w").write(t.lstrip())
PY

# 2) Rules + auto-refreshed briefing block in AGENTS.md (refresh.py keeps itself up to date from the server)
mkdir -p "$CODEX/teamvault"; printf '%s' "$TOKEN" > "$CODEX/teamvault/token"; chmod 600 "$CODEX/teamvault/token"
printf '{"server": "%s"}' "$SERVER" > "$CODEX/teamvault/config.json"
curl -fsSL "$SERVER/rules.md" -H "Authorization: Bearer $TOKEN" -o "$CODEX/teamvault/rules.md"
curl -fsSL "$SERVER/refresh.py" -H "Authorization: Bearer $TOKEN" -o "$CODEX/teamvault/refresh.py.new"
python3 -c "import sys; compile(open(sys.argv[1]).read(), 'refresh.py', 'exec')" "$CODEX/teamvault/refresh.py.new" \
  || { echo "Downloaded updater is broken — nothing changed. Tell your teamvault admin."; rm -f "$CODEX/teamvault/refresh.py.new"; exit 1; }
mv "$CODEX/teamvault/refresh.py.new" "$CODEX/teamvault/refresh.py"
rm -f "$CODEX/teamvault/refresh.sh"
printf '#!/bin/sh\nexec /usr/bin/env python3 "%s/teamvault/refresh.py"\n' "$CODEX" > "$CODEX/teamvault/refresh.sh"
chmod +x "$CODEX/teamvault/refresh.sh" "$CODEX/teamvault/refresh.py"
"$CODEX/teamvault/refresh.sh" || echo "(first refresh couldn't reach the server — it will retry every 15 min)"

# 3) Keep it fresh: launchd on macOS, cron elsewhere
if [ -n "$TEAMVAULT_NO_SCHEDULE" ]; then :
elif [ "$(uname)" = "Darwin" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  PL="$HOME/Library/LaunchAgents/com.teamvault.refresh.plist"
  cat > "$PL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.teamvault.refresh</string>
<key>ProgramArguments</key><array><string>$CODEX/teamvault/refresh.sh</string></array>
<key>StartInterval</key><integer>900</integer><key>RunAtLoad</key><true/>
</dict></plist>
EOF
  launchctl unload "$PL" 2>/dev/null || true; launchctl load "$PL"
else
  { crontab -l 2>/dev/null | grep -v teamvault/refresh.sh || true; echo "*/15 * * * * '$CODEX/teamvault/refresh.sh'"; } | crontab -
fi
echo "Connected. Restart Codex (and the Codex app) — it now reads and shares team memory automatically."

#!/usr/bin/env bash
# Stop anything still listening on the copilot's ports (8100-8103), e.g. after Ctrl+C left processes behind.
for port in 8100 8101 8102 8103; do
  if command -v taskkill >/dev/null 2>&1; then           # Windows (Git Bash)
    for pid in $(netstat -ano | grep -E "127\.0\.0\.1:$port .*LISTENING|0\.0\.0\.0:$port .*LISTENING" | awk '{print $NF}' | sort -u); do
      taskkill //PID "$pid" //F >/dev/null 2>&1 && echo "Stopped process $pid on port $port"
    done
  else                                                     # macOS / Linux
    for pid in $(lsof -ti tcp:$port 2>/dev/null); do
      kill "$pid" 2>/dev/null && echo "Stopped process $pid on port $port"
    done
  fi
done
echo "Ports 8100-8103 are free."

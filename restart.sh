#!/bin/bash
# 重启巧算后台服务，不影响系统 python 2.7
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PY=python3.6
command -v python3.6 >/dev/null 2>&1 || PY=python3

echo "stopping old process in $DIR ..."
ps -eo pid,cmd | grep "[p]ython.*server.py" | while read -r pid rest; do
    cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    if [ "$cwd" = "$DIR" ]; then
        kill "$pid" 2>/dev/null || true
        echo "  killed $pid"
    fi
done
sleep 1

echo "starting $PY server.py ..."
nohup "$PY" "$DIR/server.py" >> "$DIR/nohup.out" 2>&1 &
echo $! > "$DIR/server.pid"
sleep 1

if kill -0 "$(cat "$DIR/server.pid")" 2>/dev/null; then
    echo "running pid=$(cat "$DIR/server.pid")"
    tail -n 12 "$DIR/nohup.out"
else
    echo "start failed, see nohup.out"
    tail -n 30 "$DIR/nohup.out"
    exit 1
fi

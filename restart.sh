#!/bin/bash
# 重启巧算后台：先把端口上的旧进程停干净，再 nohup 拉起。不动系统 python 2.7。
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

PY=python3.6
command -v python3.6 >/dev/null 2>&1 || PY=python3

PORT="$(awk -F= '/^PORT=/{gsub(/\r/,""); print $2; exit}' .env 2>/dev/null)"
PORT="${PORT:-51334}"

pids_on_port() {
    ss -lntp 2>/dev/null | awk -v p=":${PORT}" '
        index($0, p) && /users:\(\("/ {
            while (match($0, /pid=[0-9]+/)) {
                print substr($0, RSTART+4, RLENGTH-4)
                $0 = substr($0, RSTART+RLENGTH)
            }
        }'
}

echo "port $PORT  DIR=$DIR"

# 1) 本目录里的 server.py
ps -eo pid,cmd | grep "[p]ython.*server.py" | while read -r pid rest; do
    cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    if [ "$cwd" = "$DIR" ]; then
        echo "stop pid $pid"
        kill "$pid" 2>/dev/null || true
    fi
done

# 2) 仍占着端口的进程（可能 cwd 对不上）
for pid in $(pids_on_port); do
    echo "stop port-holder $pid"
    kill "$pid" 2>/dev/null || true
done

# 3) 等到端口空出来，必要时再强杀
i=0
while [ $i -lt 15 ]; do
    left="$(pids_on_port)"
    if [ -z "$left" ]; then
        break
    fi
    i=$((i + 1))
    if [ $i -eq 8 ]; then
        echo "still busy, kill -9 $left"
        kill -9 $left 2>/dev/null || true
    fi
    sleep 1
done

if [ -n "$(pids_on_port)" ]; then
    echo "port $PORT still in use:"
    ss -lntp | grep ":${PORT}" || true
    exit 1
fi

# 新一轮日志，避免和上次报错混在一起
: > "$DIR/nohup.out"

echo "start $PY ..."
nohup "$PY" "$DIR/server.py" >> "$DIR/nohup.out" 2>&1 &
echo $! > "$DIR/server.pid"
sleep 1

if ! kill -0 "$(cat "$DIR/server.pid")" 2>/dev/null; then
    echo "start failed"
    cat "$DIR/nohup.out"
    exit 1
fi

echo "running pid=$(cat "$DIR/server.pid")"
cat "$DIR/nohup.out"

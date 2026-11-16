#!/usr/bin/env python3
"""Watchdog of a k6 run against the public container: samples it and its hypervisor, stops k6 on a stop criterion.

    python perf/k6/surveille.py --out <dir>/surveillance.tsv [--k6-api http://127.0.0.1:6565]

Every few seconds, ONE ssh session to the hypervisor (--ssh) runs a read-only loop (REMOTE below)
that prints: the hottest k10temp sensor of the hypervisor, its load average, the CPU of each container of the public
stack (docker stats), the CPU of sshd in the public container (--ct; cost of the SSH tunnel) and the PostgreSQL
sessions of the public base by state and wait event type. The loop stops by itself after
--duree-max seconds or when the ssh session closes.

In parallel it reads k6's REST API (k6 run -a 127.0.0.1:6565) and computes, over the last
sample interval, the request rate, the failure rate and the mean duration (deltas of the
cumulative values). The run is stopped through that API (PATCH /v1/status stopped=true, k6 still
writes its summary) when:
  - the hypervisor CPU reaches --temp-max (95 C, the PveCpuTemperatureHigh threshold);
  - the windowed failure rate reaches --erreurs-max (30 %) over at least 50 requests;
  - the clock enters Sunday 03:30-06:00 (weekly backup of the work base).
Everything is appended to --out as TSV; nothing is written on the hypervisor or in the container.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# Wrapped in a function run with stdin from /dev/null: `bash -s` reads the script from stdin, and
# a child (pct, docker) inheriting it would swallow the rest of the script.
REMOTE = r"""
main() {{
end=$(( $(date +%s) + {duree} ))
db=$(pct exec {ct} -- docker ps -q --filter name=datacommune-public-db | head -1)
while [ "$(date +%s)" -lt "$end" ]; do
  t=0
  for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$h/name" 2>/dev/null)" = k10temp ] || continue
    for f in "$h"/temp*_input; do v=$(cat "$f"); [ "$v" -gt "$t" ] && t=$v; done
  done
  load=$(cut -d' ' -f1 /proc/loadavg)
  stats=$(pct exec {ct} -- docker stats --no-stream --format '{{{{.Name}}}}={{{{.CPUPerc}}}}' | tr '\n' ' ')
  sshd=$(pct exec {ct} -- ps -C sshd -o %cpu= | awk '{{s+=$1}} END {{print s+0}}')
  # shellcheck disable=SC2016  # $POSTGRES_* expand in the db container, not here
  pg=$(pct exec {ct} -- docker exec "$db" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select coalesce(state, chr(45)) || chr(58) || coalesce(wait_event_type, chr(45)) || chr(61) || count(*) from pg_stat_activity where datname = current_database() group by state, wait_event_type"' | tr '\n' ' ')
  echo "S $(date +%s) $t $load | $stats| sshd=$sshd | $pg"
  sleep {pause}
done
}}
main </dev/null
"""

STOP_BODY = json.dumps(
    {"data": {"type": "status", "id": "default", "attributes": {"stopped": True}}}
).encode()


def k6_get(api: str, metric: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"{api}/v1/metrics/{metric}", timeout=3
        ) as response:
            return json.load(response)["data"]["attributes"]["sample"]
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None


def k6_stop(api: str, reason: str) -> None:
    request = urllib.request.Request(
        f"{api}/v1/status",
        data=STOP_BODY,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            print(f"STOP {reason} (k6 answered {response.status})", flush=True)
    except (urllib.error.URLError, OSError) as exc:
        print(
            f"STOP {reason} (k6 unreachable: {type(exc).__name__}; stop it by hand)",
            flush=True,
        )


def in_backup_window(now: dt.datetime) -> bool:
    return now.weekday() == 6 and dt.time(3, 30) <= now.time() < dt.time(6, 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--k6-api", default="http://127.0.0.1:6565")
    parser.add_argument("--ssh", default="pve02")
    parser.add_argument("--ct", type=int, default=103)
    parser.add_argument("--temp-max", type=float, default=95.0)
    parser.add_argument("--erreurs-max", type=float, default=0.30)
    parser.add_argument("--duree-max", type=int, default=1800)
    parser.add_argument("--pause", type=int, default=3)
    parser.add_argument(
        "--source-cmd", help="replaces the ssh command (local test only)"
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    remote = REMOTE.format(duree=args.duree_max, ct=args.ct, pause=args.pause)
    command = (
        shlex.split(args.source_cmd)
        if args.source_cmd
        else [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ServerAliveInterval=15",
            args.ssh,
            "bash -s",
        ]
    )
    # Binary pipes: a text-mode pipe on Windows would send the script to bash with CRLF endings.
    source = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    if not args.source_cmd:
        source.stdin.write(remote.encode("utf-8"))
    source.stdin.close()

    latest = {"temp": None, "line": ""}

    def reader() -> None:
        for raw in io.TextIOWrapper(source.stdout, encoding="utf-8", errors="replace"):
            line = raw.strip()
            if line.startswith("S "):
                parts = line.split()
                # 0: no k10temp sensor read, not a temperature.
                latest["temp"] = (
                    int(parts[2]) / 1000
                    if parts[2].isdigit() and parts[2] != "0"
                    else None
                )
                latest["line"] = line[2:]
            elif line:
                print(f"source: {line}", flush=True)

    threading.Thread(target=reader, daemon=True).start()

    stopped = False
    seen_k6 = False
    last_seen = time.monotonic()
    previous = None
    start = time.monotonic()
    with open(args.out, "a", encoding="utf-8", newline="\n") as out:
        out.write("utc\treq_s\techecs\tduree_moy_ms\ttemp_c\tdistant\n")
        while time.monotonic() - start < args.duree_max:
            time.sleep(args.pause + 2)
            reqs = k6_get(args.k6_api, "http_reqs")
            failed = k6_get(args.k6_api, "http_req_failed")
            duration = k6_get(args.k6_api, "http_req_duration")
            now = time.monotonic()
            rate = fail_rate = mean = None
            if reqs is not None and failed is not None and duration is not None:
                seen_k6, last_seen = True, now
                current = (
                    now,
                    reqs["count"],
                    failed["rate"] * reqs["count"],
                    duration["avg"] * reqs["count"],
                )
                if previous is not None and current[1] > previous[1]:
                    n = current[1] - previous[1]
                    rate = n / (current[0] - previous[0])
                    fail_rate = (current[2] - previous[2]) / n
                    mean = (current[3] - previous[3]) / n
                    if not stopped and n >= 50 and fail_rate >= args.erreurs_max:
                        k6_stop(
                            args.k6_api,
                            f"failures {fail_rate:.0%} over the last {n} requests",
                        )
                        stopped = True
                previous = current
            elif seen_k6 and now - last_seen > 30:
                print("k6 API gone for 30 s: run over", flush=True)
                break
            temp = latest["temp"]
            if not stopped and temp is not None and temp >= args.temp_max:
                k6_stop(args.k6_api, f"pve02 CPU at {temp:.1f} C")
                stopped = True
            if not stopped and in_backup_window(dt.datetime.now()):
                k6_stop(args.k6_api, "Sunday 03:30-06:00 backup window")
                stopped = True
            fields = [
                dt.datetime.now(dt.UTC).strftime("%H:%M:%S"),
                f"{rate:.1f}" if rate is not None else "-",
                f"{fail_rate:.2%}" if fail_rate is not None else "-",
                f"{mean:.0f}" if mean is not None else "-",
                f"{temp:.1f}" if temp is not None else "-",
                latest["line"],
            ]
            out.write("\t".join(fields) + "\n")
            out.flush()
            print("  ".join(fields), flush=True)
    source.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Summarise Dimplex BLE reliability from Home Assistant debug logs.

Usage (reads the HA log on stdin):
    docker logs --since 2026-11-24T18:30:00Z homeassistant 2>&1 | python3 scripts/soak_report.py

Counts per heater: polls ok/failed, connects, re-encryptions (pair ok) and their
median time, pairing failures by code, GATT failures, abnormal link losses
(bleak-esphome ``error=8``), ``ROUTE_NOT_SEEN`` polls, settle waits, stall repairs.
Needs ``custom_components.dimplex_ble`` and ``bleak_esphome`` at DEBUG.
"""

from __future__ import annotations

import re
import statistics
import sys
from collections import defaultdict

ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAC = r"([0-9A-F]{2}(?::[0-9A-F]{2}){5})"
PATTERNS = {
    "poll_ok": re.compile(r"Finished fetching Dimplex Heater (AM\d+) data .*success: True"),
    "poll_fail": re.compile(r"Finished fetching Dimplex Heater (AM\d+) data .*success: False"),
    "connect_ok": re.compile(rf"CONNECT_RESULT {MAC} ok"),
    "connect_fail": re.compile(rf"CONNECT_RESULT {MAC} failed"),
    "pair_ok": re.compile(rf"PAIR_RESULT {MAC} ok ([0-9.]+)s"),
    "pair_fail": re.compile(rf"PAIR_RESULT {MAC} failed code=(\S+)"),
    "gatt_fail": re.compile(rf"GATT {MAC} \S+(?: \S+)? failed"),
    "link_lost": re.compile(rf"{MAC}: Connection state changed to connected=False mtu=0 error=8"),
    "route_not_seen": re.compile(rf"ROUTE_NOT_SEEN {MAC}"),
    "settle": re.compile(rf"SETTLE {MAC} waiting"),
    "stall_repair": re.compile(rf"{MAC}.*stopped responding", re.IGNORECASE),
}


def main() -> None:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    pair_times: dict[str, list[float]] = defaultdict(list)
    pair_codes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    first = last = None
    for raw in sys.stdin:
        line = ANSI.sub("", raw)
        if line[:4].isdigit():
            stamp = line[:19]
            first = first or stamp
            last = stamp
        for name, pattern in PATTERNS.items():
            match = pattern.search(line)
            if not match:
                continue
            key = match.group(1)
            counts[key][name] += 1
            if name == "pair_ok":
                pair_times[key].append(float(match.group(2)))
            if name == "pair_fail":
                pair_codes[key][match.group(2)] += 1
    print(f"window: {first} .. {last}")
    columns = list(PATTERNS)
    print("key".ljust(18) + "".join(c[:12].rjust(13) for c in columns) + "  pair_med_s")
    for key in sorted(counts):
        row = key.ljust(18) + "".join(str(counts[key][c]).rjust(13) for c in columns)
        times = pair_times.get(key)
        row += f"  {statistics.median(times):.2f}" if times else "  -"
        print(row)
    for key, codes in sorted(pair_codes.items()):
        print(f"pair failure codes {key}: {dict(codes)}")


if __name__ == "__main__":
    main()

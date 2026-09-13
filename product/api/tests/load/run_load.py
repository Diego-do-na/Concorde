#!/usr/bin/env python3
"""
run_load.py — T040 concurrency + soak test against a deployed concorde-api.

(a) BUILD — Altur's client sends one call at a time from our station, so
concurrency is NOT a judging condition; the numbers this script produces are
engineering evidence for the Feasibility criterion (NFR-004/006), not a
replay of judging.

Two modes, both driving canonical Altur JSON bodies at POST /detect
(`{"call_id", "audio_base64", "sample_rate", "channels"}` — ADR-013's wire
contract, see docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf §8.1):

  concurrency  8 concurrent clients x 10 requests each (80 total) with a
               single ~180s val clip, reporting client-side p50/p95/p99 and
               the server-side p50/p95/p99 pulled from GET /metrics
               (before/after delta) for the "detect" route.

  soak         A fixed rate (default 4 req/s) sustained for a fixed duration
               (default 30 minutes), run as a background process
               (`nohup ... &`) so it never blocks the calling shell. Appends
               one JSON line per request to a log file and samples
               `ps -o rss` of the concorde-api process (over Tailscale via
               `ssh`, or locally if --local) every 30s into a second log.

Usage:
  # concurrency test (foreground, finishes in ~seconds/minutes)
  python3 run_load.py concurrency --base-url https://getconcorde.tech \
      --clip val_180s.wav --clients 8 --requests-per-client 10 \
      --out docs/load-report-concurrency.json

  # soak test (background; do this, don't block the session)
  nohup python3 run_load.py soak --base-url https://getconcorde.tech \
      --clip val_180s.wav --rate 4 --duration-s 1800 \
      --ssh-host root@100.93.147.55 --ssh-port 2222 \
      --out docs/load-soak.jsonl --rss-out docs/load-soak-rss.jsonl \
      >> docs/load-soak-runner.log 2>&1 &

Both modes print a threshold pass/fail summary at the end (for `soak`, only
once the run finishes — read the two log files directly for a live view).
Requires: httpx (`pip install httpx`), stdlib asyncio/json/statistics/subprocess.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    print("run_load.py requires httpx: pip install httpx", file=sys.stderr)
    raise

# --- thresholds (T040 DoD) --------------------------------------------------

THRESHOLD_P99_MS = 5000.0   # p99 <= 5000 ms under 8-way concurrency
THRESHOLD_RSS_MB = 1536.0   # RSS <= 1.5 GB throughout
# zero non-200 responses is checked directly, no named constant needed.


def load_clip_b64(path: str) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def canonical_body(call_id: str, audio_b64: str, sample_rate: int, channels: int) -> dict[str, Any]:
    # Canonical Altur JSON shape (ADR-013, hackmty26 commit 429adf7):
    # {"call_id", "audio_base64", "sample_rate", "channels"}.
    return {
        "call_id": call_id,
        "audio_base64": audio_b64,
        "sample_rate": sample_rate,
        "channels": channels,
    }


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    s = sorted(values)

    def pct(p: float) -> float:
        if len(s) == 1:
            return s[0]
        k = (len(s) - 1) * p
        f, c = int(k), min(int(k) + 1, len(s) - 1)
        if f == c:
            return s[f]
        return s[f] + (s[c] - s[f]) * (k - f)

    return {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99)}


async def fetch_metrics(client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
    try:
        r = await client.get(f"{base_url}/metrics", headers={"Accept": "application/json"}, timeout=10.0)
        if r.status_code == 200:
            return r.json()
    except httpx.HTTPError:
        pass
    return None


def server_route_hist(metrics_json: dict[str, Any] | None, route: str = "detect") -> dict[str, float] | None:
    if not metrics_json:
        return None
    routes = metrics_json.get("routes", {})
    return routes.get(route)


# --- concurrency mode --------------------------------------------------------

async def run_one_client(
    client: httpx.AsyncClient,
    base_url: str,
    body_template: dict[str, Any],
    n_requests: int,
    client_idx: int,
    results: list[dict[str, Any]],
) -> None:
    for i in range(n_requests):
        body = dict(body_template)
        body["call_id"] = f"load-c{client_idx}-r{i}-{int(time.time()*1000)}"
        t0 = time.perf_counter()
        status = None
        err = None
        try:
            r = await client.post(f"{base_url}/detect", json=body, timeout=35.0)
            status = r.status_code
            if status == 200:
                payload = r.json()
                if set(payload.keys()) != {"is_synthetic", "confidence"}:
                    err = f"unexpected keys: {list(payload.keys())}"
        except httpx.HTTPError as e:
            err = str(e)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        results.append({
            "client": client_idx,
            "request": i,
            "status": status,
            "latency_ms": dt_ms,
            "error": err,
            "ts": time.time(),
        })


async def run_concurrency(args: argparse.Namespace) -> dict[str, Any]:
    audio_b64 = load_clip_b64(args.clip)
    body_template = canonical_body("placeholder", audio_b64, args.sample_rate, args.channels)

    async with httpx.AsyncClient() as client:
        metrics_before = await fetch_metrics(client, args.base_url)

        results: list[dict[str, Any]] = []
        t_start = time.perf_counter()
        tasks = [
            run_one_client(client, args.base_url, body_template, args.requests_per_client, i, results)
            for i in range(args.clients)
        ]
        await asyncio.gather(*tasks)
        wall_s = time.perf_counter() - t_start

        metrics_after = await fetch_metrics(client, args.base_url)

    latencies = [r["latency_ms"] for r in results]
    statuses = [r["status"] for r in results]
    non_200 = [r for r in results if r["status"] != 200]

    client_pcts = percentiles(latencies)
    server_after = server_route_hist(metrics_after)
    server_before = server_route_hist(metrics_before)

    report = {
        "mode": "concurrency",
        "base_url": args.base_url,
        "clients": args.clients,
        "requests_per_client": args.requests_per_client,
        "total_requests": len(results),
        "wall_time_s": wall_s,
        "client_side_ms": client_pcts,
        "server_side_ms_after": server_after,
        "server_side_ms_before": server_before,
        "non_200_count": len(non_200),
        "non_200_examples": non_200[:5],
        "thresholds": {
            "p99_ms_le": THRESHOLD_P99_MS,
            "client_p99_pass": client_pcts["p99"] <= THRESHOLD_P99_MS,
            "server_p99_pass": (server_after or {}).get("p99_ms", 0.0) <= THRESHOLD_P99_MS if server_after else None,
            "zero_non_200_pass": len(non_200) == 0,
        },
    }
    return report


# --- soak mode ---------------------------------------------------------------

def sample_rss_mb(ssh_host: str | None, ssh_port: int, process_name: str) -> float | None:
    """Sample RSS (MB) of the concorde-api process, locally or via ssh (Tailscale)."""
    ps_cmd = ["ps", "-C", process_name, "-o", "rss=,pid="]
    if ssh_host:
        remote = " ".join(ps_cmd)
        cmd = ["ssh", "-p", str(ssh_port), ssh_host, remote]
    else:
        cmd = ps_cmd
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15.0)
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if not lines:
            return None
        # sum RSS across all matching PIDs (ps -C can return multiple), report KB->MB
        total_kb = sum(int(l.split()[0]) for l in lines)
        return total_kb / 1024.0
    except Exception:
        return None


async def rss_sampler(
    ssh_host: str | None,
    ssh_port: int,
    process_name: str,
    interval_s: float,
    rss_out: Path,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        rss_mb = await asyncio.to_thread(sample_rss_mb, ssh_host, ssh_port, process_name)
        with rss_out.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "rss_mb": rss_mb}) + "\n")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


async def soak_worker(
    client: httpx.AsyncClient,
    base_url: str,
    body_template: dict[str, Any],
    rate_hz: float,
    duration_s: float,
    out: Path,
) -> None:
    interval = 1.0 / rate_hz
    t_end = time.perf_counter() + duration_s
    i = 0
    while time.perf_counter() < t_end:
        t_req_start = time.perf_counter()
        body = dict(body_template)
        body["call_id"] = f"soak-{i}-{int(time.time()*1000)}"
        status = None
        err = None
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{base_url}/detect", json=body, timeout=35.0)
            status = r.status_code
        except httpx.HTTPError as e:
            err = str(e)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        with out.open("a") as f:
            f.write(json.dumps({
                "i": i, "status": status, "latency_ms": dt_ms, "error": err, "ts": time.time(),
            }) + "\n")
        i += 1
        elapsed = time.perf_counter() - t_req_start
        sleep_for = interval - elapsed
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)


async def run_soak(args: argparse.Namespace) -> dict[str, Any]:
    audio_b64 = load_clip_b64(args.clip)
    body_template = canonical_body("placeholder", audio_b64, args.sample_rate, args.channels)
    out = Path(args.out)
    rss_out = Path(args.rss_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rss_out.parent.mkdir(parents=True, exist_ok=True)

    stop_event = asyncio.Event()
    async with httpx.AsyncClient() as client:
        sampler_task = asyncio.create_task(
            rss_sampler(args.ssh_host, args.ssh_port, args.process_name, args.rss_interval_s, rss_out, stop_event)
        )
        await soak_worker(client, args.base_url, body_template, args.rate, args.duration_s, out)
        stop_event.set()
        await sampler_task

    # summarize from the log files we just wrote
    reqs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    rss_samples = [json.loads(l) for l in rss_out.read_text().splitlines() if l.strip()]
    non_200 = [r for r in reqs if r["status"] != 200]
    rss_values = [r["rss_mb"] for r in rss_samples if r["rss_mb"] is not None]
    max_rss = max(rss_values) if rss_values else None

    report = {
        "mode": "soak",
        "base_url": args.base_url,
        "rate_hz": args.rate,
        "duration_s": args.duration_s,
        "total_requests": len(reqs),
        "non_200_count": len(non_200),
        "non_200_examples": non_200[:5],
        "max_rss_mb": max_rss,
        "rss_samples": len(rss_samples),
        "thresholds": {
            "rss_mb_le": THRESHOLD_RSS_MB,
            "rss_pass": (max_rss is not None and max_rss <= THRESHOLD_RSS_MB) if max_rss is not None else None,
            "zero_non_200_pass": len(non_200) == 0,
        },
    }
    return report


# --- CLI ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", required=True, help="e.g. https://getconcorde.tech (no trailing slash)")
    common.add_argument("--clip", required=True, help="path to a ~180s val WAV clip (never committed, NFR-011)")
    common.add_argument("--sample-rate", type=int, default=8000)
    common.add_argument("--channels", type=int, default=2)

    pc = sub.add_parser("concurrency", parents=[common])
    pc.add_argument("--clients", type=int, default=8)
    pc.add_argument("--requests-per-client", type=int, default=10)
    pc.add_argument("--out", default="docs/load-report-concurrency.json")

    ps_ = sub.add_parser("soak", parents=[common])
    ps_.add_argument("--rate", type=float, default=4.0, help="requests/sec")
    ps_.add_argument("--duration-s", type=float, default=1800.0, help="30 minutes default")
    ps_.add_argument("--out", default="docs/load-soak.jsonl")
    ps_.add_argument("--rss-out", default="docs/load-soak-rss.jsonl")
    ps_.add_argument("--rss-interval-s", type=float, default=30.0)
    ps_.add_argument("--ssh-host", default=None, help="e.g. root@100.93.147.55 (Tailscale); omit for --local")
    ps_.add_argument("--ssh-port", type=int, default=2222)
    ps_.add_argument("--process-name", default="concorde-api")

    return p


async def main_async(args: argparse.Namespace) -> int:
    if args.mode == "concurrency":
        report = await run_concurrency(args)
    else:
        report = await run_soak(args)

    out_path = Path(args.out if args.mode == "concurrency" else args.out).with_suffix(".summary.json") \
        if args.mode == "soak" else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    all_pass = all(v for k, v in report["thresholds"].items() if k.endswith("_pass") and v is not None)
    print(f"\n{'PASS' if all_pass else 'FAIL'} — see {out_path}")
    return 0 if all_pass else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

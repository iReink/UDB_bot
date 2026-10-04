"""Read-only Linux CPU sample; compare CPU-time deltas, not lifetime ps averages."""
import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SERVICES = ('udb-bot', 'udb-web', 'udb-ai-groq', 'udb-rag')


def sample():
    counters = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
    services = {}
    for service in SERVICES:
        output = subprocess.check_output(['systemctl', 'show', service, '-p', 'CPUUsageNSec', '-p', 'MainPID'], text=True)
        values = dict(line.split('=', 1) for line in output.splitlines())
        services[service] = dict(pid=int(values['MainPID']), cpu=int(values['CPUUsageNSec']))
    return counters, services


def measure(seconds):
    start = time.monotonic()
    before, services_before = sample()
    time.sleep(seconds)
    after, services_after = sample()
    elapsed = time.monotonic() - start
    # guest and guest_nice are already included in user/nice.
    delta = [b-a for a, b in zip(before[:8], after[:8])]
    total = sum(delta)
    result = dict(at=datetime.now(timezone.utc).isoformat(), seconds=round(elapsed, 3),
                  cpu_percent=round(100*(total-delta[3]-delta[4]-delta[7])/total, 3),
                  steal_percent=round(100*delta[7]/total, 3),
                  iowait_percent=round(100*delta[4]/total, 3), services={})
    for name, current in services_after.items():
        previous = services_before[name]
        result['services'][name] = (round(100*(current['cpu']-previous['cpu'])/1e9/elapsed, 3)
                                   if current['pid']==previous['pid'] and current['cpu']>=previous['cpu'] else None)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error('seconds must be 1..3600')
    print(json.dumps(measure(args.seconds), ensure_ascii=False), flush=True)

"""One-shot monitor for cron/external runner; nonzero exit means alert.

No credentials/URLs are printed. Run on a separate host for outage detection.
MONITOR_BASE_URL=https://... MONITOR_TOKEN=... python scripts/monitor.py
"""
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def evaluate(metrics, previous_5xx=0, max_pending=200, max_pending_age=3600):
    reasons = []
    if metrics.get('database') != 'ok':
        reasons.append('database_not_ready')
    if metrics.get('worker', {}).get('status') != 'ok':
        reasons.append('worker_unhealthy')
    if metrics.get('failed_notifications', 0):
        reasons.append('dead_letter_notifications')
    pending = metrics.get('pending_notifications', 0)
    if pending > max_pending:
        reasons.append('notification_backlog')
    if (metrics.get('oldest_pending_seconds') or 0) > max_pending_age:
        reasons.append('notification_delivery_lag')
    if pending and not metrics.get('worker', {}).get('delivery_enabled'):
        reasons.append('delivery_disabled_with_pending_notifications')
    total = metrics.get('requests_this_process', {}).get('5xx', 0)
    # Counters may reset at process restart. Multi-worker metrics are not aggregate.
    if (total - previous_5xx if total >= previous_5xx else total) > 0:
        reasons.append('new_5xx')
    return reasons


def main():
    base = os.environ.get('MONITOR_BASE_URL', '').rstrip('/')
    token = os.environ.get('MONITOR_TOKEN') or os.environ.get('MONITOR_ADMIN_TOKEN', '')
    parsed = urlparse(base)
    if not token or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        print(json.dumps({'status':'error','reasons':['monitor_configuration']}))
        return 2
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1')):
        print(json.dumps({'status':'error','reasons':['https_required']}))
        return 2
    state = Path(os.environ.get('MONITOR_STATE_PATH', '.monitor-state.json'))
    try:
        previous = json.loads(state.read_text()).get('last_5xx', 0) if state.exists() else 0
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(NoRedirect())
        def get(path, auth=False):
            headers = {'X-Admin-Token':token} if auth else {}
            request = urllib.request.Request(base + path, headers=headers)
            with opener.open(request, timeout=10) as response:
                return json.load(response)
        ready = get('/ready')
        metrics = get('/api/admin/metrics', True)
        reasons = evaluate(metrics, previous, int(os.environ.get('MONITOR_MAX_PENDING','200')),
                           int(os.environ.get('MONITOR_MAX_PENDING_AGE','3600')))
        if ready.get('status') != 'ready':
            reasons.append('api_not_ready')
        state.parent.mkdir(parents=True, exist_ok=True)
        # No secrets in state. Atomic replacement avoids truncation on crashes.
        with tempfile.NamedTemporaryFile(mode='w', dir=state.parent, delete=False) as target:
            json.dump({'last_5xx':metrics.get('requests_this_process',{}).get('5xx',0)}, target)
            temporary = target.name
        os.replace(temporary, state)
        print(json.dumps({'status':'alert' if reasons else 'ok', 'reasons':reasons}))
        return 1 if reasons else 0
    except Exception:
        print(json.dumps({'status':'alert','reasons':['monitor_check_failed']}))
        return 1


if __name__ == '__main__':
    sys.exit(main())

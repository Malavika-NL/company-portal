import os
import socket
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


_application_locks = defaultdict(threading.Lock)
_healthy_until = {}


def _health_cache_key(service):
    return service['name'], int(service['port'])


def _recently_healthy(service):
    return (
        _healthy_until.get(_health_cache_key(service), 0) > time.monotonic()
        and is_port_open(service['port'])
    )


def _remember_healthy(service):
    ttl = float(getattr(settings, 'PORTAL_SERVICE_HEALTH_CACHE_SECONDS', 5))
    _healthy_until[_health_cache_key(service)] = time.monotonic() + max(0, ttl)


def _forget_health(service):
    _healthy_until.pop(_health_cache_key(service), None)


def is_port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', int(port))) == 0


def health_ok(service):
    if service.get('port_only_health'):
        return is_port_open(service['port'])

    method = service.get('health_method', 'GET')
    data = b'{}' if method == 'POST' else None
    headers = {'Accept': service.get('accept', 'application/json')}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = Request(service['health_url'], data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=2) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except (URLError, TimeoutError, OSError):
        return False
    return int(status) in set(service.get('healthy_statuses', [200]))


def start_service(service):
    cwd = service.get('cwd')
    if cwd and not os.path.isdir(cwd):
        raise FileNotFoundError(cwd)

    creationflags = 0
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

    log_dir = Path(settings.BASE_DIR) / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_name = service['name'].lower().replace(' ', '_') + '.log'
    with open(log_dir / log_name, 'ab') as log_file:
        env = os.environ.copy()
        env.update(service.get('env', {}))
        subprocess.Popen(
            service['command'],
            cwd=cwd or None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
        )


def stop_port_process(port):
    if os.name != 'nt':
        return
    command = (
        f"$processIds = @(Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); "
        "foreach ($processId in $processIds) { "
        "if ($processId -gt 0) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue } }"
    )
    subprocess.run(
        ['powershell', '-NoProfile', '-Command', command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def ensure_application_services(application, include_frontend=True):
    if not settings.PORTAL_AUTO_START_CRMS:
        return True, ''

    with _application_locks[application]:
        services = [
            service for service in settings.CRM_LOCAL_SERVICES.get(application, [])
            if include_frontend or service.get('component') != 'frontend'
        ]
        failed = []

        for service in services:
            if _recently_healthy(service):
                continue
            if health_ok(service):
                _remember_healthy(service)
                continue

            _forget_health(service)
            if is_port_open(service['port']) and settings.PORTAL_AUTO_RESTART_UNHEALTHY_CRMS:
                stop_port_process(service['port'])
                time.sleep(0.25)

            if not is_port_open(service['port']):
                try:
                    start_service(service)
                except OSError:
                    failed.append(service['name'])
                    continue

            deadline = time.monotonic() + settings.PORTAL_AUTO_START_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if health_ok(service):
                    _remember_healthy(service)
                    break
                time.sleep(0.2)
            else:
                failed.append(service['name'])

    if failed:
        return False, ', '.join(failed)
    return True, ''


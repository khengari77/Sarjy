"""Offline boundary test against the actual production web server."""
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest


def test_built_web_requires_password_before_proxying():
    if not shutil.which('node') or not Path('apps/web/build/index.js').exists():
        if os.environ.get('CI'):
            pytest.fail('CI must build the frontend before this test')
        pytest.skip('Build the frontend first to run the production boundary test')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ | {
        'HOST': '127.0.0.1', 'PORT': str(port),
        'DEVELOPMENT_PROTECTED': 'true', 'DEVELOPMENT_USER': 'tester',
        'DEVELOPMENT_PASSWORD': 'test-only-password', 'WEB_SERVICE_SECRET': 'test-only-proxy-secret',
        'API_INTERNAL_URL': 'http://127.0.0.1:1',
    }
    process = subprocess.Popen(['node', 'server.js'], cwd='apps/web', env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=2) as client:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail('Protected web server exited before readiness')
                try:
                    if client.get('/').status_code == 401:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail('Web server did not become ready')
            for path in ('/', '/api/v1/conversations', '/api/v1/demo-session', '/_app/version.json'):
                response = client.get(path)
                assert response.status_code == 401
                assert 'Basic' in response.headers['www-authenticate']
                assert 'set-cookie' not in response.headers
            assert client.get('/', auth=('tester', 'wrong')).status_code == 401
            assert client.get('/', auth=('tester', 'test-only-password')).status_code == 200
            # Authorized request reaches the proxy, whose deliberately absent backend returns 503.
            assert client.get('/api/v1/conversations', auth=('tester', 'test-only-password')).status_code == 503
    finally:
        process.terminate()
        process.wait(timeout=10)

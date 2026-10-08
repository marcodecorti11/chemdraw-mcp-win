"""A stdio MCP server must survive lazy native-extension imports during a tool call.

On Windows, loading numpy's native DLLs while another thread is blocked reading the stdin
pipe (which is what the stdio transport does) hangs indefinitely: the DLL runtime queries the
standard input handle and waits behind the pending synchronous read. Reproduced without
ChemDraw. The server therefore reads the pipe through a private duplicate and points the
process-wide standard input at NUL before serving.
"""
import json
import subprocess
import sys
import threading
import time

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CHILD = r'''
import sys, threading, time
from chemdraw_macos.server import isolate_stdio_pipe
isolate_stdio_pipe()
got = []
threading.Thread(target=lambda: got.append(sys.stdin.readline()), daemon=True).start()
time.sleep(0.5)                      # the reader is now blocked on the (private) pipe
import numpy                         # hangs here without isolation on Windows
print('numpy ok', flush=True)
while not got: time.sleep(0.05)
print('got ' + got[0].strip(), flush=True)
'''


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows synchronous pipe I/O serialization')
def test_isolated_stdin_survives_native_import_with_pending_read():
    pytest.importorskip('numpy')
    child = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    lines = []
    threading.Thread(target=lambda: [lines.append(l.strip()) for l in child.stdout], daemon=True).start()
    try:
        deadline = time.monotonic() + 30
        while 'numpy ok' not in lines and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        assert 'numpy ok' in lines, lines
        child.stdin.write('hello\n'); child.stdin.flush()   # the private duplicate still carries MCP input
        assert child.wait(timeout=10) == 0
        time.sleep(0.1)
        assert 'got hello' in lines, lines
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)


def test_isolation_is_a_no_op_off_windows(monkeypatch):
    from chemdraw_macos import server
    monkeypatch.setattr(server.sys, 'platform', 'darwin')
    stdin = sys.stdin
    server.isolate_stdio_pipe()
    assert sys.stdin is stdin


@pytest.mark.asyncio
async def test_first_rdkit_tool_call_over_stdio_completes():
    pytest.importorskip('rdkit')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'chemdraw_macos.server', '--profile', 'full'])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            await anyio.sleep(0.5)  # let the server's stdin reader block before the first native import
            with anyio.fail_after(60):
                result = await session.call_tool('chemdraw_identify', {'value': 'CCO', 'input_format': 'smiles'})
            assert not result.isError, result
            payload = result.structuredContent or json.loads(result.content[0].text)
            assert 'CCO' in json.dumps(payload)

"""Small asyncio TCP edge for local multi-port validation.

This is not the production proxy (nginx/Caddy is); it exists so the
independent-port recipe can be exercised on a machine without either package.
It accepts one connection, picks a backend, and pipes bytes until EOF.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys


class Edge:
    def __init__(self, backends: list[tuple[str, int]]) -> None:
        self.backends = backends
        self._next = itertools.cycle(range(len(backends)))

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        host, port = self.backends[next(self._next)]
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=5
            )
            await asyncio.gather(
                self._pipe(reader, upstream_writer),
                self._pipe(upstream_reader, writer),
            )
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
            writer.close()

    @staticmethod
    async def _pipe(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
        try:
            writer.write_eof()
        except (OSError, RuntimeError):
            pass


async def serve(backends: list[tuple[str, int]], port: int) -> None:
    edge = Edge(backends)
    server = await asyncio.start_server(edge.handle, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backends", required=True, help="host:port,host:port")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    backends = []
    for item in args.backends.split(","):
        host, _, raw_port = item.partition(":")
        backends.append((host, int(raw_port)))
    try:
        asyncio.run(serve(backends, args.port))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

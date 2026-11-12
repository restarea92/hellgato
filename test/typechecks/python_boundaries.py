"""Compile-time consumers of the real private Python boundaries; not executed."""

from dataclasses import dataclass
from typing import assert_type

from hellgato_host.startup_status import HostLifetime, StartupStatus, decode_status, read_status
from hellgato_mirabox.protocol import (ProtocolError, RpcMessage, check_deadline,
                                      jpeg_size, validate_contract, validate_rpc_message)


@dataclass(frozen=True)
class Host:
    pid: int
    created: int


def physical_boundary(data: bytes, wire_message: object) -> RpcMessage:
    assert_type(jpeg_size(data), tuple[int, int])
    assert_type(ProtocolError('bad frame').rpc_code, int)
    assert_type(ProtocolError('bad frame').code, str)
    frame = {'width': 1, 'height': 1}
    assert_type(validate_contract('DisplayFrame', frame), dict[str, int])
    check_deadline({'deadlineAt': 123})
    return validate_rpc_message(wire_message)


def host_boundary(record: bytes) -> StartupStatus | None:
    host: HostLifetime = Host(pid=17, created=234)
    assert_type(decode_status(record, host), StartupStatus)
    assert_type(decode_status(record, host).ready, bool)
    return read_status(host)

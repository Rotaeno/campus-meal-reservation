"""Read-only Windows TCP observations, restricted to the lab's loopback app ports."""
import ctypes
import os
import socket
import struct


def snapshot(ports):
    if os.name != 'nt':
        return {'available': False, 'reason': 'Windows TCP table observer'}
    api = ctypes.windll.iphlpapi.GetExtendedTcpTable
    size = ctypes.c_ulong(0)
    api(None, ctypes.byref(size), False, 2, 5, 0)
    buffer = ctypes.create_string_buffer(size.value)
    code = api(buffer, ctypes.byref(size), False, 2, 5, 0)
    if code:
        return {'available': False, 'errorCode': code}
    data = buffer.raw
    count = struct.unpack_from('<I', data)[0]
    states, pending, connections = {}, [], 0
    for index in range(count):
        state, local_address, local_raw, remote_address, remote_raw, pid = struct.unpack_from('<6I', data, 4 + index * 24)
        local, remote = socket.ntohs(local_raw & 65535), socket.ntohs(remote_raw & 65535)
        if local not in ports and remote not in ports:
            continue
        if socket.inet_ntoa(struct.pack('<I', local_address)) != '127.0.0.1':
            continue
        role = 'server' if local in ports else 'client'
        key = role + ':' + str(state)
        states[key] = states.get(key, 0) + 1
        connections += 1
        if state in (3, 4):  # SYN_SENT/SYN_RECEIVED, not yet an established HTTP request.
            pending.append({'localPort': local, 'remotePort': remote, 'state': state, 'pid': pid})
    return {'available': True, 'connections': connections, 'states': states, 'pendingHandshakes': pending}

"""A small protobuf codec driven by the recovered .proto schemas.

Only what these config blobs need: varints, length-delimited fields,
fixed32/64, proto3 defaults, repeated and optional fields, nested messages
and enums.  Schemas are read from the .proto files recovered from the
descriptors embedded in Flydigi.SharedResources.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- wire layer

def read_varint(buf: bytes, i: int) -> tuple[int, int]:
    val = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]; i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def write_varint(val: int) -> bytes:
    if val < 0:                       # protobuf encodes negatives as 64-bit
        val += 1 << 64
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def parse_raw(buf: bytes) -> dict[int, list]:
    """Decode to {field_number: [raw values]} with no schema."""
    out: dict[int, list] = {}
    i = 0
    while i < len(buf):
        key, i = read_varint(buf, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v = buf[i:i + ln]; i += ln
        elif wt == 5:
            v = buf[i:i + 4]; i += 4
        elif wt == 1:
            v = buf[i:i + 8]; i += 8
        else:
            raise ValueError(f"unsupported wire type {wt}")
        out.setdefault(fn, []).append(v)
    return out


# ------------------------------------------------------------- schema layer

SCALARS = {"int32", "int64", "uint32", "uint64", "sint32", "sint64",
           "bool", "string", "bytes", "float", "double",
           "fixed32", "fixed64", "sfixed32", "sfixed64"}

PACKABLE = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool"}


@dataclass
class Field:
    name: str
    number: int
    type: str
    repeated: bool = False
    optional: bool = False


@dataclass
class Message:
    name: str
    fields: dict[int, Field] = field(default_factory=dict)

    def by_name(self, name: str) -> Field | None:
        for f in self.fields.values():
            if f.name == name:
                return f
        return None


class Schema:
    """Messages and enums parsed from the recovered .proto files."""

    def __init__(self):
        self.messages: dict[str, Message] = {}
        self.enums: dict[str, dict[str, int]] = {}

    # -- loading -------------------------------------------------------

    def load_dir(self, path: str) -> "Schema":
        for fn in sorted(os.listdir(path)):
            if fn.endswith(".proto"):
                self.load_file(os.path.join(path, fn))
        return self

    def load_file(self, path: str) -> None:
        stack: list = []
        with open(path) as fh:
            for line in fh:
                line = line.split("//")[0].strip()
                if not line:
                    continue
                m = re.match(r"message\s+(\w+)\s*\{", line)
                if m:
                    msg = Message(m.group(1))
                    self.messages[msg.name] = msg
                    stack.append(msg)
                    continue
                m = re.match(r"enum\s+(\w+)\s*\{", line)
                if m:
                    name = m.group(1)
                    self.enums[name] = {}
                    stack.append(("enum", name))
                    continue
                if line.startswith("}"):
                    if stack:
                        stack.pop()
                    continue
                if not stack:
                    continue
                top = stack[-1]
                if isinstance(top, tuple):
                    m = re.match(r"(\w+)\s*=\s*(-?\d+)\s*;", line)
                    if m:
                        self.enums[top[1]][m.group(1)] = int(m.group(2))
                    continue
                m = re.match(r"(repeated\s+|optional\s+)?([\w.]+)\s+(\w+)\s*=\s*(\d+)\s*;", line)
                if m:
                    qual, typ, name, num = m.groups()
                    qual = (qual or "").strip()
                    top.fields[int(num)] = Field(
                        name=name, number=int(num), type=typ.split(".")[-1],
                        repeated=(qual == "repeated"),
                        optional=(qual == "optional"),
                    )

    # -- introspection -------------------------------------------------

    def kind(self, typ: str) -> str:
        if typ in SCALARS:
            return "scalar"
        if typ in self.enums:
            return "enum"
        if typ in self.messages:
            return "message"
        return "unknown"

    def enum_name(self, typ: str, value: int) -> str:
        for k, v in self.enums.get(typ, {}).items():
            if v == value:
                return k
        return str(value)

    # -- decoding ------------------------------------------------------

    def decode(self, msg_name: str, buf: bytes) -> dict:
        msg = self.messages.get(msg_name)
        if msg is None:
            raise KeyError(f"unknown message {msg_name}")
        raw = parse_raw(buf)
        out: dict = {}
        for fn, values in raw.items():
            f = msg.fields.get(fn)
            if f is None:
                out.setdefault("_unknown", {})[fn] = values
                continue
            decoded = []
            packed = (f.repeated
                      and (f.type in PACKABLE or self.kind(f.type) == "enum"))
            for v in values:
                if packed and isinstance(v, bytes):
                    # proto3 packs repeated scalars *and* repeated enums
                    i = 0
                    while i < len(v):
                        x, i = read_varint(v, i)
                        if self.kind(f.type) == "enum":
                            decoded.append(
                                EnumVal(f.type, x, self.enum_name(f.type, x)))
                        else:
                            decoded.append(self._scalar(f.type, x))
                    continue
                decoded.append(self._value(f, v))
            out[f.name] = decoded if f.repeated else decoded[-1]
        return out

    def _scalar(self, typ: str, v):
        if typ == "bool":
            return bool(v)
        if typ in ("sint32", "sint64"):
            return (v >> 1) ^ -(v & 1)
        if typ in ("int32", "int64") and v >= 1 << 63:
            return v - (1 << 64)
        return v

    def _value(self, f: Field, v):
        k = self.kind(f.type)
        if k == "message":
            return self.decode(f.type, v) if isinstance(v, bytes) else v
        if k == "enum":
            return EnumVal(f.type, v, self.enum_name(f.type, v))
        if f.type == "string":
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else v
        if f.type == "bytes":
            return v
        return self._scalar(f.type, v)

    # -- encoding ------------------------------------------------------

    def encode(self, msg_name: str, data: dict) -> bytes:
        msg = self.messages.get(msg_name)
        if msg is None:
            raise KeyError(f"unknown message {msg_name}")
        out = bytearray()
        for fn in sorted(msg.fields):
            f = msg.fields[fn]
            if f.name not in data or data[f.name] is None:
                continue
            values = data[f.name] if f.repeated else [data[f.name]]
            if not values:
                continue
            if f.repeated and (f.type in PACKABLE
                               or self.kind(f.type) == "enum"):
                body = bytearray()
                for v in values:
                    n = v.value if isinstance(v, EnumVal) else (
                        1 if v is True else 0 if v is False else int(v))
                    body += write_varint(n)
                out += write_varint((f.number << 3) | 2)
                out += write_varint(len(body)) + bytes(body)
                continue
            for v in values:
                out += self._encode_one(f, v)
        return bytes(out)

    def _encode_one(self, f: Field, v) -> bytes:
        k = self.kind(f.type)
        if k == "message":
            body = self.encode(f.type, v) if isinstance(v, dict) else bytes(v)
            return write_varint((f.number << 3) | 2) + write_varint(len(body)) + body
        if f.type == "string":
            body = v.encode("utf-8")
            return write_varint((f.number << 3) | 2) + write_varint(len(body)) + body
        if f.type == "bytes":
            return write_varint((f.number << 3) | 2) + write_varint(len(v)) + bytes(v)
        if k == "enum":
            n = v.value if isinstance(v, EnumVal) else int(v)
            return write_varint(f.number << 3) + write_varint(n)
        if f.type == "bool":
            return write_varint(f.number << 3) + write_varint(1 if v else 0)
        if f.type in ("sint32", "sint64"):
            n = int(v)
            return write_varint(f.number << 3) + write_varint((n << 1) ^ (n >> 63))
        return write_varint(f.number << 3) + write_varint(int(v))


@dataclass
class EnumVal:
    type: str
    value: int
    name: str

    def __repr__(self) -> str:
        return f"{self.name}({self.value})"

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other) -> bool:
        if isinstance(other, EnumVal):
            return self.value == other.value and self.type == other.type
        if isinstance(other, int):
            return self.value == other
        if isinstance(other, str):
            return self.name == other
        return NotImplemented


_DEFAULT_SCHEMA: Schema | None = None
PROTO_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "docs", "proto")


def default_schema() -> Schema:
    global _DEFAULT_SCHEMA
    if _DEFAULT_SCHEMA is None:
        path = os.environ.get("FLYDIGI_PROTO_DIR", PROTO_DIR)
        _DEFAULT_SCHEMA = Schema().load_dir(os.path.normpath(path))
    return _DEFAULT_SCHEMA

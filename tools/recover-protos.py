#!/usr/bin/env python3
"""Recover .proto schemas from the base64 FileDescriptorProto blobs that
protobuf's C# codegen embeds in each *Reflection.cs.

Implements just enough of descriptor.proto to re-emit readable schema text.
"""
import base64
import re
import sys
import os

# ---- generic wire-format reader -------------------------------------------

def read_varint(b, i):
    val = 0; shift = 0
    while True:
        x = b[i]; i += 1
        val |= (x & 0x7F) << shift
        if not (x & 0x80):
            return val, i
        shift += 7

def parse(b):
    """Return {field_number: [values]}; length-delimited stay as bytes."""
    out = {}
    i = 0
    while i < len(b):
        key, i = read_varint(b, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(b, i)
        elif wt == 2:
            ln, i = read_varint(b, i)
            v = b[i:i + ln]; i += ln
        elif wt == 5:
            v = b[i:i + 4]; i += 4
        elif wt == 1:
            v = b[i:i + 8]; i += 8
        else:
            break
        out.setdefault(fn, []).append(v)
    return out

def s(x):
    return x.decode('utf-8', 'replace')

# ---- descriptor.proto field numbers ---------------------------------------

TYPES = {1:'double',2:'float',3:'int64',4:'uint64',5:'int32',6:'fixed64',
         7:'fixed32',8:'bool',9:'string',10:'group',11:'message',12:'bytes',
         13:'uint32',14:'enum',15:'sfixed32',16:'sfixed64',17:'sint32',
         18:'sint64'}
LABELS = {1:'', 2:'', 3:'repeated '}

def field_str(fb):
    f = parse(fb)
    name = s(f[1][0]) if 1 in f else '?'
    num = f[3][0] if 3 in f else 0
    label = LABELS.get(f[4][0], '') if 4 in f else ''
    if 6 in f:                      # type_name for message/enum
        typ = s(f[6][0]).lstrip('.')
        typ = typ.split('.')[-1]
    else:
        typ = TYPES.get(f[5][0], '?') if 5 in f else '?'
    # proto3 optional presence
    if 17 in f and f[17][0]:
        label = 'optional '
    return f"  {label}{typ} {name} = {num};"

def enum_str(eb, indent="") :
    e = parse(eb)
    name = s(e[1][0]) if 1 in e else '?'
    lines = [f"{indent}enum {name} {{"]
    for vb in e.get(2, []):
        v = parse(vb)
        vn = s(v[1][0]) if 1 in v else '?'
        num = v[2][0] if 2 in v else 0
        lines.append(f"{indent}  {vn} = {num};")
    lines.append(f"{indent}}}")
    return "\n".join(lines)

def message_str(mb, indent=""):
    m = parse(mb)
    name = s(m[1][0]) if 1 in m else '?'
    lines = [f"{indent}message {name} {{"]
    for fb in m.get(2, []):
        lines.append(indent + field_str(fb))
    for nb in m.get(3, []):                    # nested_type
        lines.append(message_str(nb, indent + "  "))
    for eb in m.get(4, []):                    # enum_type
        lines.append(enum_str(eb, indent + "  "))
    lines.append(f"{indent}}}")
    return "\n".join(lines)

def decode_file(blob):
    fd = parse(blob)
    out = []
    if 2 in fd:
        out.append(f"package {s(fd[2][0])};")
    if 1 in fd:
        out.insert(0, f"// file: {s(fd[1][0])}")
    for dep in fd.get(3, []):
        out.append(f'import "{s(dep)}";')
    out.append("")
    for eb in fd.get(5, []):
        out.append(enum_str(eb)); out.append("")
    for mb in fd.get(4, []):
        out.append(message_str(mb)); out.append("")
    return "\n".join(out)

# ---- driver ---------------------------------------------------------------

def extract_b64(text):
    """Collect every string literal inside the FromBase64String(...) call."""
    anchor = text.find("FromBase64String(")
    if anchor < 0:
        return None
    i = anchor + len("FromBase64String")
    depth = 0
    chunks = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "([{":
            depth += 1; i += 1; continue
        if ch in ")]}":
            depth -= 1; i += 1
            if depth <= 0:
                break
            continue
        if ch == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 2
                    continue
                buf.append(text[j]); j += 1
            chunks.append("".join(buf))
            i = j + 1
            continue
        i += 1
    if not chunks:
        return None
    return "".join(chunks)


def main():
    srcdir, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    ok = fail = 0
    for fn in sorted(os.listdir(srcdir)):
        if not fn.endswith("Reflection.cs"):
            continue
        text = open(os.path.join(srcdir, fn), encoding='utf-8', errors='replace').read()
        b64 = extract_b64(text)
        if not b64:
            fail += 1; continue
        try:
            blob = base64.b64decode(b64)
            proto = decode_file(blob)
        except Exception as exc:
            print(f"  !! {fn}: {exc}"); fail += 1; continue
        name = fn[:-len("Reflection.cs")]
        open(os.path.join(outdir, name + ".proto"), "w").write(proto + "\n")
        ok += 1
    print(f"decoded {ok} descriptors, {fail} failed -> {outdir}")

main()

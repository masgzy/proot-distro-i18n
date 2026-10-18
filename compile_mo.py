"""Compile a .po file to a .mo file using Python's built-in msgfmt logic."""
import ast
import struct
import array

def compile_po(po_path, mo_path):
    """Compile .po to .mo using the same algorithm as msgfmt.py."""
    messages = {}

    with open(po_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Parse .po file
    section = None
    msgid = []
    msgstr = []
    line_no = 0

    for line in lines:
        line_no += 1
        line = line.rstrip('\n')

        if line == '':
            if section == 'msgstr':
                # Store the message
                m_id = ''.join(msgid)
                m_str = ''.join(msgstr)
                # Empty msgid is the metadata header — must be kept
                if m_str:
                    messages[m_id] = m_str
                section = None
                msgid = []
                msgstr = []
            continue

        if line.startswith('#'):
            continue

        if line.startswith('msgid '):
            if section == 'msgstr':
                m_id = ''.join(msgid)
                m_str = ''.join(msgstr)
                # Empty msgid is the metadata header — must be kept
                if m_str:
                    messages[m_id] = m_str
                msgid = []
                msgstr = []
            section = 'msgid'
            msgid.append(_parse_quoted(line[6:]))
        elif line.startswith('msgstr '):
            section = 'msgstr'
            msgstr.append(_parse_quoted(line[7:]))
        elif line.startswith('"'):
            if section == 'msgid':
                msgid.append(_parse_quoted(line))
            elif section == 'msgstr':
                msgstr.append(_parse_quoted(line))

    # Last entry
    if section == 'msgstr':
        m_id = ''.join(msgid)
        m_str = ''.join(msgstr)
        if m_str:
            messages[m_id] = m_str

    # Write .mo file
    _write_mo(messages, mo_path)
    print(f"Compiled {len(messages)} messages to {mo_path}")


def _parse_quoted(s):
    """Parse a quoted string from a .po file line."""
    s = s.strip()
    if s.startswith('"'):
        s = s[1:]
    if s.endswith('"'):
        s = s[:-1]
    # Unescape
    result = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            nc = s[i + 1]
            if nc == 'n':
                result.append('\n')
            elif nc == 't':
                result.append('\t')
            elif nc == 'r':
                result.append('\r')
            elif nc == '\\':
                result.append('\\')
            elif nc == '"':
                result.append('"')
            else:
                result.append(nc)
            i += 2
        else:
            result.append(c)
            i += 1
    return ''.join(result)


def _write_mo(messages, mo_path):
    """Write messages dict to a .mo file."""
    # Sort by msgid
    keys = sorted(messages.keys())

    offsets = []
    ids = b''
    strs = b''

    for key in keys:
        msgstr = messages[key]
        # Encode
        encoded_id = key.encode('utf-8')
        encoded_str = msgstr.encode('utf-8')

        offsets.append((len(ids), len(encoded_id), len(strs), len(encoded_str)))
        ids += encoded_id + b'\x00'
        strs += encoded_str + b'\x00'

    # Build the .mo file structure
    # Layout: header(28) | key_table(N*8) | value_table(N*8) | key_strings | value_strings
    keystart = 7 * 4                          # 28
    valuestart = keystart + 16 * len(keys)    # 28 + 16*N — start of key strings
    strstart = valuestart + len(ids)          # start of value strings

    # Header
    output = struct.pack(
        'Iiiiiii',
        0x950412de,           # Magic
        0,                    # Version
        len(keys),            # Number of entries
        7 * 4,                # Offset of key table
        7 * 4 + len(keys) * 8,  # Offset of value table
        0,                    # Size of hash table
        0,                    # Offset of hash table
    )

    # Key table: (length, offset) — offsets point into key strings area
    for o1, l1, o2, l2 in offsets:
        output += struct.pack('ii', l1, o1 + valuestart)

    # Value table: (length, offset) — offsets point into value strings area
    for o1, l1, o2, l2 in offsets:
        output += struct.pack('ii', l2, o2 + strstart)

    # Key strings
    output += ids

    # Value strings
    output += strs

    with open(mo_path, 'wb') as f:
        f.write(output)


if __name__ == '__main__':
    compile_po(
        'proot_distro/locales/zh_CN/LC_MESSAGES/proot_distro.po',
        'proot_distro/locales/zh_CN/LC_MESSAGES/proot_distro.mo',
    )

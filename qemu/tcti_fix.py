# Copyright (c) 2026 maao514 (Pallas Agent). SPDX-License-Identifier: GPL-2.0-only
"""Fix vector compares in the interpreter of UTM SE 5.0.8's QEMU (qemu 10.0.12-utm).

tcg/aarch64-tcti/tcg-target.c.inc, INDEX_op_cmp_vec: the cases for TCG_COND_LT/LE and LTU/LEU were
written with each other's gadget (LT emitted "b >= a", LE "b > a"), so the guest's `cmlt v, #0` was
true for elements equal to zero. Rust's HashMap looks for free slots with that instruction: one
occupied slot in 128 was taken for free, and Rust programs (ripgrep) lost entries and crashed.

In the compiled library that switch is a table of 14 16-bit branch offsets indexed by condition - 2
(LT=2, LE=7, LTU=10, LEU=15). Exchanging the LT/LE and LTU/LEU entries is the source fix exactly:
both cases of a pair pass the same operands in the same order and differ only in the gadget.
The table is found by content, and the code that reads it is checked before anything is written.
"""
import struct
import sys

ORIGINAL = (0, 924, 1828, 1828, 941, 953, 918, 930, 959, 947, 1828, 1828, 912, 906)
PATCHED = (953, 924, 1828, 1828, 941, 0, 918, 930, 906, 947, 1828, 1828, 912, 959)
# sub x8, x10, #2 ; cmp x8, #13: the switch on the condition
DISPATCH = bytes.fromhex("480900d1" "1f3500f1")


def fix_vector_compare(path):
    """Patches the library in place. Returns False when it already was; raises when it is not the expected build."""
    data = bytearray(open(path, "rb").read())
    if data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("QEMU library is not a thin 64-bit Mach-O file")
    original, patched = struct.pack("<14H", *ORIGINAL), struct.pack("<14H", *PATCHED)
    if data.count(patched) == 1 and data.count(original) == 0:
        return False
    if data.count(original) != 1:
        raise ValueError(f"QEMU compare table found {data.count(original)} times: not the UTM SE 5.0.8 library")
    at = data.index(original)
    # The code that reads the table: adrp/add to it, then adr to the base the offsets count from.
    read = False
    for use in range(0, at, 4):
        if data[use:use + 8] != DISPATCH:
            continue
        adrp, add, adr = struct.unpack_from("<3I", data, use + 12)
        if adrp & 0x9F000000 != 0x90000000 or add & 0xFFC00000 != 0x91000000 or adr & 0x9F000000 != 0x10000000:
            continue
        pages = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
        read |= ((((use + 12) >> 12) + pages) << 12) + ((add >> 10) & 0xFFF) == at
    if not read:
        raise ValueError("QEMU compare table is not read by the expected switch")
    data[at:at + len(patched)] = patched
    open(path, "wb").write(data)
    return True


if __name__ == "__main__":
    print("patched" if fix_vector_compare(sys.argv[1]) else "already patched")

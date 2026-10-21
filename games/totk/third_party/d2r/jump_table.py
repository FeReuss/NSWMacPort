"""Validate registered signed-offset dispatches before emitting landing pads."""
import struct


def signed_table_span(image, site, text_end, descriptor):
    base, table, offsets, dispatch = descriptor
    assert image[site - 16:site + 4].hex() == dispatch, f'signed-table dispatch {site:x}'
    assert struct.unpack_from('<' + 'i' * len(offsets), image, table) == offsets, f'signed-table entries {site:x}'
    targets = [base + offset for offset in offsets]
    assert targets and all(0 <= target < text_end and target % 4 == 0 for target in targets), f'signed-table targets {site:x}'
    return min(targets), (max(targets) - min(targets)) // 4 + 1

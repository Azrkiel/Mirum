"""Compare two binary STLs: bytes, facets in file order, and facets as sets."""
import sys

import numpy as np

REC = np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")])


def load(path):
    d = np.memmap(path, dtype=np.uint8, mode="r")
    count = int(np.frombuffer(d[80:84], np.uint32)[0])
    return bytes(d[:80]), count, np.frombuffer(d[84 : 84 + 50 * count], REC)


def sorted_rows(verts):
    # Sort lexicographically on raw float bits so equal facets line up.
    bits = verts.view(np.uint32).reshape(len(verts), 9)
    return verts[np.lexsort(bits.T[::-1])]


ha, na, a = load(sys.argv[1])
hb, nb, b = load(sys.argv[2])
print("header_equal", ha == hb, repr(ha[:40]), repr(hb[:40]))
print("tri_counts", na, nb)
if na != nb:
    sys.exit("different triangle counts: no facet comparison")

va, vb = a["v"], b["v"]
same_order = np.all(va == vb, axis=1)
print("facets_equal_in_file_order", int(same_order.sum()), "of", na)
print("normals_equal_in_file_order", int(np.all(a["n"] == b["n"], axis=1).sum()), "of", na)
print("attr_equal_in_file_order", int((a["a"] == b["a"]).sum()), "of", na)
if not same_order.all():
    i = int(np.argmax(~same_order))
    print("first_diff_facet", i, va[i].tolist(), vb[i].tolist())
    print("max_abs_vertex_delta_file_order_mm", float(np.abs(va.astype(np.float64) - vb.astype(np.float64)).max()))

sa, sb = sorted_rows(va), sorted_rows(vb)
same_set = np.all(sa == sb, axis=1)
print("facets_equal_as_set", int(same_set.sum()), "of", na)
if not same_set.all():
    print("max_abs_vertex_delta_sorted_mm", float(np.abs(sa.astype(np.float64) - sb.astype(np.float64)).max()))

The equipment-bay worker vendors the Python kernel from Azrkiel/mirum-kernel at
`25c232fdfc7114dd371a2c964f417eb98e8d4a71`. The adjacent Python package is an
unmodified snapshot. The two upstream JSON fixtures live under
`engineering/fixtures/equipment_bay`. Updating these files requires updating
`KERNEL_REVISION` and replaying the equipment-bay tests. Mirum owns review,
publication and promotion.

The editable plate worker vendors only `mirum_kernel/editable_plate.py` as
`mirum_editable_plate.py` from kernel commit
`6a68e99c67b99375f2a49a794f5d44464c6307e3`. The product's MEL fixtures and
native acceptance tests bind it to the editable plate capability and its
reviewed width edits. [M1 acceptance](../../docs/plans/checked-engineering-v1/phase-3-framework-language/handoffs/editable-plate.md)

The M3 structural slice vendors `beam_fea.py` and `structural.py` from
Azrkiel/mirum-kernel at `79f3517f3d447fceb27927427955390bfdd1a813`.
The package initializer also comes from that revision and loads bay CAD
dependencies only when the exchange or inspection API is called. Mirum’s
reference beam entry point calls this same solver. [M3 verification](../../docs/plans/checked-engineering-v1/phase-3-framework-language/handoffs/structural-cantilever.md)

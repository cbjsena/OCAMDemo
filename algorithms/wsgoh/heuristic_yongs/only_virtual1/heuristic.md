# only_virtual1 Heuristic

This document describes `only_virtual1`, which maps to the original
`algorithms/yongs/only_virtual` implementation.

## Core Behavior

`only_virtual1` is a conservative construction heuristic.

1. It declares selectable positions for new proformas.
2. It classifies actual vessels by current assignment, dry-dock, and redelivery
   status.
3. Assigned vessels keep sailing their current lane whenever possible.
4. If dry-dock or redelivery forces a vessel to leave service, the current
   in-lane schedule is split at a feasible transshipment point.
5. The actual vessel keeps the prefix and sails to dry-dock or redelivery.
6. The remaining suffix becomes a virtual-vessel schedule.
7. Every newly declared future proforma position is covered by a virtual vessel.

So the heuristic intentionally exposes virtual holes. It is useful as a stable
seed because it keeps actual-vessel obligations simple and makes missing service
fragments explicit.

## Variant Lever

The original code always chooses:

```text
sorted(available_positions)[:own_vessel_count]
```

`wsgoh/heuristic_yongs` creates variants by changing the selectable
`available_positions` in a deep-copied instance before calling the original
solver. The solver still sees a valid Yongs-style input, but the declared
position set changes, which changes the virtual service fragments available to
downstream pattern generation.


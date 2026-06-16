# only_virtual2 Heuristic

This document describes `only_virtual2`, which maps to the original
`algorithms/yongs/only_virtual2` implementation.

## Core Behavior

`only_virtual2` starts from the same construction idea as `only_virtual1`.

1. It declares selectable positions for new proformas.
2. It builds actual-vessel schedules for currently assigned vessels.
3. It splits schedules when dry-dock or redelivery requires an actual vessel to
   leave service.
4. It initially creates virtual schedules for uncovered service fragments.

Then it adds a surplus-vessel replacement pass.

For each new declared position and then for each virtual schedule, it searches
for an actual vessel whose schedule is idle or out-of-lane during the target
window. The candidate must be able to reposition into the target start, perform
the service, and reconnect to its next obligation within the speed limit. It
also checks capacity and reefer compatibility.

If such a vessel exists, the target service is inserted into that actual
vessel's schedule and the virtual schedule is avoided or removed.

## Variant Lever

The surplus-vessel pass is sensitive to the target service windows. By changing
the declared selectable positions before running the original solver, the
wrapper changes which virtual holes are created and which actual idle gaps can
cover them.

This makes `only_virtual2` variants more aggressive than `only_virtual1`
variants: a different position seed can also change actual-vessel insertion
decisions.


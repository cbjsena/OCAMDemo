# only_virtual1 Variants

The available `only_virtual1` variants are:

| Variant | Position rule | Effect |
| --- | --- | --- |
| `only_virtual1_lowest` | lowest available positions | Original Yongs behavior. |
| `only_virtual1_highest` | highest available positions | Moves declared future services later in the position cycle. |
| `only_virtual1_spread` | evenly spread positions | Produces separated virtual holes across the cycle. |
| `only_virtual1_offset_1` | cyclic window shifted by 1 | Keeps a compact set but changes the phase of selected positions. |
| `only_virtual1_offset_2` | cyclic window shifted by 2 | Second compact shifted seed. |

For example, if one lane-version has:

```text
available_positions = [1,2,3,4,5,6,7,8]
own_vessel_count = 3
```

then the variant wrapper feeds these position sets into the original Yongs
solver:

```text
lowest   -> [1,2,3]
highest  -> [6,7,8]
spread   -> [1,4,8]
offset_1 -> [2,3,4]
offset_2 -> [3,4,5]
```

All variants preserve the same actual-vessel construction logic. They differ in
which selectable services become declared future services, and therefore in the
virtual schedules emitted by the heuristic.

# only_virtual2 Variants

The available `only_virtual2` variants are:

| Variant | Position rule | Effect |
| --- | --- | --- |
| `only_virtual2_lowest` | lowest available positions | Original Yongs behavior plus surplus-vessel replacement. |
| `only_virtual2_highest` | highest available positions | Tests late-cycle targets against actual idle gaps. |
| `only_virtual2_spread` | evenly spread positions | Creates dispersed targets and can unlock different surplus vessels. |
| `only_virtual2_offset_1` | cyclic window shifted by 1 | Compact shifted target set for surplus insertion. |
| `only_virtual2_offset_2` | cyclic window shifted by 2 | Second compact shifted target set. |

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

The current variant layer does not rewrite the nested surplus-vessel tie-breaker
inside the original Yongs solver. Instead, it changes the declared target set
fed into that solver. This keeps the implementation low-risk while still
producing materially different heuristic seeds.

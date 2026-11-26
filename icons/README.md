# Custom icons (optional)

`icons/units/` ships with 112 character portraits fetched from Fire Emblem Wiki
(resized to 120×120 PNG). Items still get a coloured dot by type unless you
drop PNG files in `icons/items/`. You can also override or add unit portraits
in `icons/units/`.

| Folder | File name | Shown |
|---|---|---|
| `items/` | the item id (`cac4f645.png`) **or** its English name in lower case with `_` for spaces (`iron_sword.png`) | next to the item in every list, scaled to ~22 px |
| `units/` | the unit id (`3d853178.png`), its display name (`凯伊.png`), its English name / lower-case slug (`Cai.png`, `cai.png`), or its `PID_` (`PID_カイ.png`) | in the unit/support headers (~60 px) and unit lists (~20 px) |

Ids are shown in the *Id* column of the item catalogue. PNG only (Tk reads it
natively); square images look best.

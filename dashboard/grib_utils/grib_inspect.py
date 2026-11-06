#!/usr/bin/env python3
"""Summarise the contents of a GRIB file using earthkit-data.

Prints the message count, the time/level-type combinations present, the key
grid and encoding metadata, and one row per parameter with its levels.
Optionally prints min / area-weighted mean / max for chosen levels.

Examples:
    python grib_inspect.py file.grib2
    python grib_inspect.py file.grib2 --stats 850 500 250
    python grib_inspect.py file.grib2 --stats 500 --param t u v
"""
import argparse
from collections import defaultdict

import numpy as np
import earthkit.data as ekd

HEADER_KEYS = [
    "centre", "edition", "marsClass", "marsStream", "marsType",
    "gridType", "Ni", "Nj", "iDirectionIncrementInDegrees",
    "latitudeOfFirstGridPointInDegrees", "latitudeOfLastGridPointInDegrees",
    "longitudeOfFirstGridPointInDegrees", "longitudeOfLastGridPointInDegrees",
    "packingType", "bitsPerValue",
]


def meta(field, key):
    """Return a GRIB key, or 'n/a' if the message doesn't have it."""
    try:
        return field.metadata(key)
    except Exception:
        return "n/a"


def level_sort_key(level):
    """Sort numeric levels by value (1000 after 925), anything else afterwards."""
    return (0, level, "") if isinstance(level, (int, float)) else (1, 0, str(level))


def weighted_mean(field, values):
    """Mean weighted by cos(latitude), so polar points aren't over-counted."""
    lats = np.asarray(field.geography.latitudes()).reshape(values.shape)
    w = np.cos(np.deg2rad(lats))
    ok = np.isfinite(values)
    return float((values[ok] * w[ok]).sum() / w[ok].sum())


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("file", help="path to a GRIB file")
    p.add_argument("--stats", nargs="+", type=int, metavar="LEVEL",
                   help="print min/mean/max for fields on these levels")
    p.add_argument("--param", nargs="+", metavar="SHORTNAME",
                   help="limit --stats to these parameters (e.g. t u v)")
    args = p.parse_args()

    fields = list(ekd.from_source("file", args.file).to_fieldlist())
    if not fields:
        raise SystemExit("no GRIB messages found")
    print(f"File:     {args.file}")
    print(f"Messages: {len(fields)}\n")

    # Time / level-type combinations
    combos = sorted({(meta(f, "dataDate"), meta(f, "dataTime"), meta(f, "step"),
                      meta(f, "stepType"), meta(f, "typeOfLevel")) for f in fields},
                    key=str)
    print("Date      Time  Step  StepType  LevelType")
    for d, t, s, st, lt in combos:
        print(f"{d!s:<9} {t!s:>4}  {s!s:>4}  {st!s:<8}  {lt}")

    # Grid and encoding, taken from the first message
    print("\nGrid / encoding (from first message):")
    for k in HEADER_KEYS:
        print(f"  {k:<36} {meta(fields[0], k)}")

    # One row per parameter
    params = {}
    levels = defaultdict(set)
    for f in fields:
        s = meta(f, "shortName")
        params.setdefault(s, (meta(f, "name"), meta(f, "units"), meta(f, "paramId")))
        levels[s].add(meta(f, "level"))
    print(f"\nParameters ({len(params)}):")
    print(f"  {'short':<7} {'paramId':>7}  {'units':<12} {'name':<28} levels")
    for s, (name, units, pid) in params.items():
        lv = ", ".join(str(x) for x in sorted(levels[s], key=level_sort_key))
        print(f"  {s:<7} {pid!s:>7}  {units!s:<12} {name!s:<28} {lv}")

    if args.stats:
        wanted = set(args.stats)
        print("\nStatistics (mean is cos(latitude)-weighted):")
        print(f"  {'level':>5} {'short':<7} {'min':>12} {'mean':>12} {'max':>12}")
        rows = [f for f in fields
                if meta(f, "level") in wanted
                and (not args.param or meta(f, "shortName") in args.param)]
        rows.sort(key=lambda f: (-meta(f, "level"), str(meta(f, "shortName"))))
        for f in rows:
            v = f.to_numpy()
            print(f"  {meta(f, 'level'):>5} {meta(f, 'shortName'):<7} "
                  f"{np.nanmin(v):>12.4g} {weighted_mean(f, v):>12.4g} {np.nanmax(v):>12.4g}")
        if not rows:
            print("  (no matching fields)")


if __name__ == "__main__":
    main()

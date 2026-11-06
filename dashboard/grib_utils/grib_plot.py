#!/usr/bin/env python3
"""Plot one GRIB field on a global map using earthkit-plots.

Examples:
    python grib_plot.py file.grib2 t850.png                   # 850 hPa temperature
    python grib_plot.py file.grib2 t500.png --param t --level 500
    python grib_plot.py file.grib2 u250.png --param u --level 250 --units km/h

Temperatures (any field stored in kelvin, such as t, 2t, 2d or skt) are shown
in Celsius by default, with earthkit's built-in style; use --units to change.
Asking for other units switches to a generic colour scale fitted to the data, because
earthkit-plots' built-in styles keep their native-unit contour levels.
cartopy downloads coastline data on first use. If that fails with
CERTIFICATE_VERIFY_FAILED, set SSL_CERT_FILE to certifi's bundle:
    export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")
"""
import argparse

import earthkit.data as ekd
import earthkit.plots as ekp
from earthkit.plots.metadata import units as _ekp_units


def _patch_unit_parsing():
    """Work around an earthkit-plots 1.0.4 bug with multi-term unit strings.

    earthkit-plots turns spaces in unit strings into '.', which pint rejects,
    so any field with units like 'm s**-1', 'Pa s**-1' or 'kg kg**-1' crashes
    with "DefinitionSyntaxError: unknown operator '.'". Using '*' instead of a
    space means the same thing and pint accepts it. Remove once fixed upstream.
    """
    original = _ekp_units._pintify
    if getattr(original, "_grib_utils_patched", False):
        return

    def pintify(unit_str):
        if isinstance(unit_str, str):
            unit_str = " ".join(unit_str.split()).replace(" ", "*")
        return original(unit_str)

    pintify._grib_utils_patched = True
    _ekp_units._pintify = pintify


_patch_unit_parsing()


def level_label(level_type, level, name):
    """How the level reads in the title: '850 hPa', '100 m', or nothing for single-level fields."""
    if level_type == "isobaricInhPa":
        return f"{level} hPa"
    if level_type == "heightAboveGround":
        # '2 metre temperature' already says where it is; '100 metre ...' likewise
        return "" if f"{level} metre" in name else f"{level} m above ground"
    if level_type == "soilLayer":
        return f"soil layer {level}"
    if level_type == "depthBelowLandLayer":
        return f"{level} m below ground"
    # surface, meanSea, entireAtmosphere, nominalTop, ...: the name says it all
    return ""


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("file", help="path to a GRIB file")
    p.add_argument("output", help="output image, e.g. map.png")
    p.add_argument("--param", default="t", help="GRIB shortName (default: t)")
    p.add_argument("--level", type=int, default=850, help="level (default: 850)")
    p.add_argument("--units", help="display units, e.g. km/h, g/kg (default: celsius for "
                                   "fields in kelvin, otherwise native)")
    p.add_argument("--style", choices=["auto", "generic"],
                   help="auto = earthkit's built-in style for the parameter; "
                        "generic = colour scale fitted to the data. Default: auto, "
                        "or generic when --units changes the units (built-in "
                        "styles' levels are not rescaled by unit conversion)")
    p.add_argument("--title", help="custom title (earthkit-plots format keys allowed)")
    p.add_argument("--dpi", type=int, default=150)
    args = p.parse_args()

    fl = ekd.from_source("file", args.file).to_fieldlist()
    matches = [f for f in fl
               if f.metadata("shortName") == args.param and f.metadata("level") == args.level]
    if not matches:
        raise SystemExit(f"no field with shortName={args.param} at level {args.level}")
    field = matches[0]

    default_units = "celsius" if field.metadata("units") == "K" else None
    units = args.units or default_units
    style = args.style or ("generic" if args.units and args.units != default_units else "auto")

    name = field.metadata("name")
    where = level_label(field.metadata("typeOfLevel"), args.level, name)
    title = args.title or (f"{name}{' at ' + where if where else ''} — "
                           f"{{time:%Y-%m-%d %H UTC}}, step {field.metadata('step')}")

    chart = ekp.Map()
    plot_kwargs = {"units": units} if units else {}
    if style == "generic":
        # zorder=1 keeps the field beneath coastlines and gridlines
        chart.quickplot(field, style=None, zorder=1, **plot_kwargs)
    else:
        chart.quickplot(field, **plot_kwargs)
    chart.coastlines()
    chart.gridlines()
    if args.units:
        # earthkit-plots can mislabel converted units (g/kg shows as "dimensionless")
        chart.legend(label=f"{name} ({args.units})")
    else:
        chart.legend()
    chart.title(title)
    chart.save(args.output, dpi=args.dpi)
    print(f"saved {args.output}")


if __name__ == "__main__":
    main()

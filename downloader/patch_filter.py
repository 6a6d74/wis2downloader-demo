"""Build-time patch to wis2downloader 0.3.2: only queue notifications whose path matches a filter.

WIS2_DATA_ID_FILTER (a regular expression, from DATA_ID_FILTER in .env) is searched for in each
notification's properties.data_id and its canonical link's href. Notifications that match
neither are dropped before they reach the download queue. Empty or unset: no filtering.

ECMWF's deterministic topic carries IFS 0.1° (ifs/0p1/oper), IFS 0.25° (ifs/0p25/oper), waves
(ifs/0p25/wave) and AIFS (aifs-single/0p25/oper). They share metadata_ids, and 0.25° IFS and
AIFS files have identical names, so the path is the only reliable way to pick one product.
"""
import pathlib

import wis2downloader.subscriber as sub

path = pathlib.Path(sub.__file__)
src = path.read_text()

imports_old = "import json\n"
imports_new = "import json\nimport os\nimport re\n"
hook_old = "        job = {\n            'topic': msg.topic,\n"
hook_new = '''        # demo patch (patch_filter.py): keep only notifications whose path matches the filter
        _flt = os.environ.get("WIS2_DATA_ID_FILTER", "")
        if _flt:
            try:
                _p = json.loads(msg.payload)
                _paths = [_p.get("properties", {}).get("data_id", "")] + [
                    l.get("href", "") for l in _p.get("links", []) if l.get("rel") == "canonical"]
            except Exception:
                _paths = []
            if not any(re.search(_flt, s or "") for s in _paths):
                LOGGER.info(f"Filtered out (not {_flt}): {(_paths or ['?'])[0].rsplit('/', 1)[-1]}")
                return

        job = {
            'topic': msg.topic,
'''
for old in (imports_old, hook_old):
    if src.count(old) != 1:
        raise SystemExit(f"patch_filter.py: expected text not found once in {path}: {old!r}")
src = src.replace(imports_old, imports_new, 1).replace(hook_old, hook_new, 1)
path.write_text(src)
print(f"patched {path}")

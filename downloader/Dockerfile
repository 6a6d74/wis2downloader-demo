# wis2downloader 0.3.2 (PyPI): the release with the `wis2downloader add-subscription` CLI.
FROM python:3.12-slim

RUN pip install --no-cache-dir "wis2downloader==0.3.2" "gunicorn==26.2.0"

# 0.3.2 names saved files by media type and maps unknown types to ".bin". Add the
# other GRIB and BUFR media types used on WIS2 (saved as ".grib2" / ".bufr4"). ECMWF uses a
# type 0.3.2 already maps to ".grib", so ECMWF files are saved as ".grib".
RUN f=$(python -c "import wis2downloader.downloader as d; print(d.__file__)") && \
    sed -i 's|"text/plain": "txt"|"text/plain": "txt",\n        "application/grib": "grib2",\n        "application/grib2": "grib2",\n        "application/x-grib2": "grib2",\n        "application/bufr": "bufr4",\n        "application/x-bufr4": "bufr4"|' "$f" && \
    python -c "from wis2downloader.downloader import map_media_type as m; \
assert m('application/grib') == 'grib2' and m('application/bufr') == 'bufr4'"

# Only queue notifications whose data_id / canonical href matches WIS2_DATA_ID_FILTER
# (DATA_ID_FILTER in .env; default ifs/0p1/oper): see patch_filter.py.
COPY patch_filter.py /tmp/patch_filter.py
RUN python /tmp/patch_filter.py && rm /tmp/patch_filter.py && \
    python -c "import wis2downloader.subscriber"

WORKDIR /app
ENV WIS2DOWNLOADER_CONFIG=/app/config.json \
    PYTHONUNBUFFERED=1

# Port 5000 inside the container is the CLI's default, so
# `wis2downloader add-subscription --topic ...` works without --port.
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=3)"

# One worker only: the app runs its MQTT client and download threads in-process.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", \
     "--timeout", "120", "wis2downloader.app:app"]

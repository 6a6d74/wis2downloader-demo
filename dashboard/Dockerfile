# Dashboard: standard-library web server, plus the earthkit packages that the
# grib-utils scripts in grib_utils/ need for "Inspect & plot".
FROM python:3.12-slim

# The versions grib-utils was tested with: grib_plot.py patches earthkit-plots 1.0.4.
RUN pip install --no-cache-dir "earthkit-data==1.3.1" "earthkit-plots==1.0.4" certifi

# Download the coastlines at build time, so plotting needs no network at run time.
ENV CARTOPY_DATA_DIR=/opt/cartopy
RUN python -c "import cartopy, cartopy.feature as cf; cartopy.config['data_dir'] = '/opt/cartopy'; \
[list(cf.NaturalEarthFeature('physical', 'coastline', s).geometries()) for s in ('110m', '50m', '10m')]" && \
    chmod -R a+rX /opt/cartopy

# The container runs as the host user, who has no home directory in the image.
# USER: earthkit asks for the user name, and the host uid has no entry in /etc/passwd.
ENV HOME=/tmp USER=dashboard MPLCONFIGDIR=/tmp/matplotlib PYTHONUNBUFFERED=1
WORKDIR /app
CMD ["python", "-u", "/app/server.py"]

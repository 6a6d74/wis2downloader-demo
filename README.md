# WIS2 Downloader demo

A self-contained local deployment of the [WIS2 Downloader](https://github.com/World-Meteorological-Organization/wis2downloader). It subscribes to ECMWF's deterministic medium-range forecasts on Météo-France's Global Broker and accumulates the GRIB2 files they announce. A purge deletes files older than 36 hours and keeps the pile under 20 GB.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/wis2downloader-demo-architecture-dark.png">
  <img src="docs/wis2downloader-demo-architecture.png" alt="Architecture: ECMWF publishes notifications flagged cache: false. Global Caches republish them via Météo-France's Global Broker without copying the data. On the host computer, wis2downloader (in Docker) receives them over MQTT, downloads each GRIB2 file straight from ECMWF and writes it to ./downloads. The dashboard (shown in the browser on localhost:8088) scans that folder and can inspect and plot any GRIB file in it, and the purger deletes files older than 36 hours or beyond the 20 GB cap.">
</picture>

ECMWF flags its notifications `"cache": false`. Global Caches therefore republish them without copying the data, and each notification's canonical link points at ECMWF's own WIS2 node, so the GRIB2 files are downloaded straight from ECMWF. On 2026-11-06 every download came from `data.ecmwf.int`. The terminal, browser and `./downloads` folder are on the host computer. Only the three containers run in Docker.

| Container | What it does | In the browser |
|---|---|---|
| `downloader` | wis2downloader **0.3.2** (PyPI), the release with the `wis2downloader add-subscription` CLI. It connects to `globalbroker.meteo.fr:443` (MQTT over WebSockets, `everyone`/`everyone`) and saves each announced file to `./downloads/<yyyy>/<mm>/<dd>/<topic>/`. | API and Swagger UI: <http://localhost:5050/swagger> |
| `purger` | Deletes files downloaded more than `RETENTION_HOURS` ago (36 h by default), every 10 minutes. If the pile still exceeds `MAX_DOWNLOAD_GB` (20 GB), the oldest files go first. wis2downloader 0.3.2 never deletes anything itself. | shown on the dashboard |
| `dashboard` | Shows the configuration, the subscriptions and the pile: file count, size, a size-over-time chart and the newest files. It also lets you browse and download the files, and **Inspect & plot** any GRIB file. | <http://localhost:8088> |

**What is wis2downloader, and what isn't.** Only the `downloader` container is wis2downloader: the unmodified PyPI package, apart from the small patch described under *Notes*. Its own web interface is the API and Swagger page on port 5050. The dashboard (including Inspect & plot) and the purger were written for this demo and aren't part of wis2downloader, so say so when presenting. The current wis2downloader (1.0 beta) has its own web UI and Grafana dashboards, but no `add-subscription` CLI (see *Notes*).

## Run it

This demo is for **Linux** only. You need Docker Engine with the Compose plugin (`docker compose`), and a user that can run `docker`. Windows isn't supported.

1. **Set your user and group IDs.** The containers run as you, so the files they write in `./downloads` and `./state` are yours and you can delete them. Check `id -u` and `id -g`. If either isn't 1000, set `HOST_UID` and `HOST_GID` in `.env` to match.
2. **Start it:**
   ```bash
   git clone https://github.com/6a6d74/wis2downloader-demo.git
   cd wis2downloader-demo
   docker compose up -d --build
   ```

Keep `./downloads` and `./state` in place before starting: if they're missing, Docker creates them owned by root.

The first `--build` takes a few minutes: the dashboard image installs earthkit for *Inspect & plot* (about 1.3 GB) and downloads coastline data. Later starts reuse it.

Open <http://localhost:8088>.

## Demo steps

1. **Show the configuration.** On the dashboard, the *Configuration* card shows:
   - the Global Broker, login and download directory
   - the 36-hour retention and the 20 GB cap

   Expand *downloader/config.json* to show the downloader's actual config file. The purge settings are in `.env`.
2. **Add the subscription.** Run:
   ```bash
   docker compose exec downloader \
     wis2downloader add-subscription \
     --topic "cache/a/wis2/int-ecmwf/data/core/weather/prediction/forecast/medium-range/deterministic/global"
   ```
   *Subscriptions* on the dashboard turns green and lists the topic. The same list is at <http://localhost:5050/subscriptions>, or from `docker compose exec downloader wis2downloader list-subscriptions`. Subscriptions survive restarts: they're kept in `./state/mqtt_session.json`.
3. **Show the pile.**
   - The tiles show the count and size of files on disk.
   - *Downloaded data on disk* charts the size, one sample a minute. It climbs while ECMWF releases a forecast run, then flattens at about 20 GB as the purger removes the oldest files to stay under the cap.
   - *Most recent files* and **Browse the downloaded files** show the GRIB2 files themselves, in folders by date and topic. They're saved with a `.grib` extension (see *Notes*), for example `20261106000000-102h-oper-fc-pl.grib`.
   - The same files are in `./downloads` on this computer, so you can also open them in a file manager or with GRIB tools such as `grib_ls`.

4. **Look inside a GRIB file.** On the downloads page, press **Inspect & plot** beside any GRIB file (folders don't have the button). An overlay covers the page and shows, side by side:
   - **left:** the file's metadata from `grib_inspect.py`: message count, run, step and level types, grid and encoding (for the 0.1° files: GRIB edition 2, regular lat/lon, 3600 × 1801 points), and every parameter with its levels;
   - **right:** a world map of one field from `grib_plot.py`, with *Parameter* and *Level* menus to replot another.

   It opens on 850 hPa temperature for pressure-level files and 2 m temperature for surface files. Temperatures are shown in °C. A map takes about 5–15 seconds. Close the overlay with **Close**, Esc, or a click outside it; **← Back to the dashboard** at the top of the downloads page returns to the dashboard.

ECMWF publishes on this topic only when a forecast run is released, in bursts a few times a day. For a full pile during the demo, start the stack and add the subscription several hours beforehand. The pile reaches the 20 GB cap within a few hours. Keep the computer from suspending meanwhile, for example with `systemd-inhibit --what=sleep:idle sleep 12h` in a terminal.

## How much data

Measured on 5–6 November 2026. The topic carries four ECMWF products from each of the four daily runs (00z, 06z, 12z, 18z):

| Product | File name ends | Size per file | Files per run | Per run |
|---|---|---|---|---|
| IFS 0.1° pressure levels | `-oper-fc-pl` | ~370 MB | 49 (0–144 h, 3-hourly) | ~18 GB |
| IFS 0.1° surface | `-oper-fc-sfc` | ~95 MB | 49 | ~4.6 GB |
| IFS 0.25° | `-oper-fc` | ~57–100 MB | 49–85 (to 144 h or 360 h) | ~5–6 GB |
| Wave, 0.1° and 0.25° | `-wave-fc-sfc`, `-wave-fc` | 4–27 MB | 49–85 | ~0.3–1.6 GB |

That is about **30 GB per run, or roughly 120 GB a day**.

**Ball-park figure for the audience:** over 24 hours this one topic delivers roughly **120 GB of unique GRIB2 data in about 1,200 files**, counting each file once. That's an average of about 1.4 MB/s (11 Mbit/s), arriving in bursts as each run is released. The notifications are several times more numerous: each file was announced about 7 times overnight (once per Global Cache republishing it), roughly 8,000 notifications a day. These figures come from about a day of observation (the 5 November 18z run complete, the 6 November 00z run in progress), so treat them as ±20%. With one download worker, wis2downloader managed about 8 MB/s, about 28 GB an hour. That's plenty over a day, but it falls behind while a run is being published, so files can arrive a few hours after ECMWF releases them.

**Backlog.** While a run is being published, notifications arrive faster than one worker can download, so they queue in the downloader's memory. On 6 November the queue reached about 950 notifications at 10:10Z, roughly three quarters of them repeats that are skipped instantly, and was empty about an hour later, well before the next run. Files therefore land up to a few hours after ECMWF releases them. That doesn't matter for this demo. The queue length is `queue_size` on <http://localhost:5050/metrics>.

**Restart only when the queue is empty.** The downloader keeps its MQTT session on the broker (`clean_session=False`, QoS 1), so notifications published while it's down are delivered when it reconnects. But notifications already in its in-memory queue have been acknowledged, and a restart (`docker compose up -d`, `restart`, `down`) drops them and their files.

**Possible follow-ups,** not needed for the demo:
1. **More workers without duplicates.** Patch 0.3.2 so a worker skips a file another worker is already downloading, which also closes the race behind the 2.7× duplicates (see *Notes*). Then raise `download_workers`: with 4 workers the overall rate was about 17 MB/s, against about 8 MB/s with 1.
2. **Fewer products.** 0.3.2 can't filter within a topic, so this is also a patch: skip file names matching a pattern. The 0.1° pressure-level files are about 60% of the volume; keeping only the 0.1° surface files (about 4.6 GB a run) would bring each run in within minutes, at the cost of the upper-air fields.
3. **Show the queue on the dashboard,** from the downloader's `/metrics`, so presenters can see when a restart is safe.

So the **20 GB cap, not the 36-hour retention, is what limits the pile**: 20 GB is less than one run, and the purger keeps roughly the latest 4–5 hours of files. Overnight on 5–6 November it removed 75 GB, all because of the cap.

**20 GB is fine for the demo.** The point is to see GRIB files arriving and to look inside the ones that have arrived, not to keep a complete archive: a few hours of files, a few dozen of each product, is plenty to browse and inspect. Raise `MAX_DOWNLOAD_GB` only if you need more and the disk has room: holding 36 hours would take about 180 GB.

## Change the purge

Edit `.env`, then run `docker compose up -d`:

```
RETENTION_HOURS=36        # delete files downloaded more than this long ago
MAX_DOWNLOAD_GB=20        # safety cap: oldest files go first above this; 0 = no cap
PURGE_INTERVAL_MINUTES=10
```

At ECMWF's volume (see *How much data*), the cap does the work and `RETENTION_HOURS` only matters if you raise the cap a lot or subscribe to a quieter topic. The default 20 GB is enough for the demo.

## Stop and clean up

```bash
docker compose down                  # stop; files and subscriptions are kept (check the queue first: see *How much data*)
rm -rf downloads/* state/*           # forget files, subscriptions and history
```

## Notes

- **Why 0.3.2.** The current wis2downloader (1.0 beta, on GitHub) is a larger stack: Redis, Celery workers, Grafana, Loki, Prometheus and a NiceGUI web UI. It has no `wis2downloader add-subscription` command; subscriptions go through its REST API or UI. Its retention setting is in whole days, so it can't do 36 hours.
- **File extensions and one patch to 0.3.2.** 0.3.2 names saved files by media type, not by the name in the URL, and calls anything it doesn't recognise `.bin`. ECMWF announces a GRIB media type that 0.3.2 already maps to `.grib`, so ECMWF's `.grib2` files are saved as `.grib`. The image adds other GRIB and BUFR media types used on WIS2 (see `downloader/Dockerfile`), which are saved as `.grib2` and `.bufr4`.
- **One download worker.** `download_workers` is 1 in `downloader/config.json`. Each Global Cache republishes every notification, so each file is announced several times. 0.3.2 skips a file that already exists, but with several workers, two can fetch the same file at once before either has saved it. With 4 workers, ECMWF data was downloaded 2.7 times over; with 1 there were no duplicates (2,926 repeat notifications skipped in 10 hours). The cost is speed: see *How much data*.
- **Duplicates under different names.** 0.3.2 skips a file only if one with the same name already exists that day. If two Global Caches name the same file differently, it's saved twice. This was seen with NOAA test data, not ECMWF.
- **Inspect & plot.** These are dashboard features, not part of wis2downloader.
  - **Scripts:** the dashboard runs copies of `grib_inspect.py` and `grib_plot.py` from grib-utils, unchanged, in `dashboard/grib_utils/`. They were copied on 6 November 2026, after two fixes to `grib_plot.py` in grib-utils: fields stored in kelvin are shown in °C with earthkit's own temperature colours (before, only `t` was), and titles name the level only where it adds something (`2 metre temperature`, not `… at heightAboveGround 2`). If grib-utils changes, copy the scripts again.
  - **Image:** `dashboard/Dockerfile` (about 1.3 GB) installs the versions grib-utils was tested with, earthkit-data 1.3.1 and earthkit-plots 1.0.4, and downloads the coastlines at build time, so plotting needs no network. It sets `HOME` and `USER`, because earthkit looks up the user name and the host user has no entry in the image.
  - **Server:** `/api/inspect` and `/api/plot` run the scripts on a file under `./downloads` only, and only for `.grib`, `.grib2`, `.grb` and `.grb2` files. The overlay's parameter list comes from the GRIB headers, read with ecCodes. One plot runs at a time, and the newest 100 maps are cached in the container. A file purged while you look at it gives a "may have been purged" message.
- **Running as you.** All three containers run as `HOST_UID:HOST_GID` from `.env`. The downloader also sets `HOME=/tmp`, because gunicorn keeps a control socket in the home directory and that user has none in the image.
- **Ports.**
  - Host port 5050 maps to the downloader's 5000, which is often taken on the host. Inside the container, 5000 is the CLI's default, so `add-subscription` needs no `--port`.
  - The dashboard is on 8088.
  - Both ports listen on `127.0.0.1` only.

## Licence and credits

The code in this repository is licensed under the [Apache License 2.0](LICENSE). That includes the copies of `grib_inspect.py` and `grib_plot.py` in `dashboard/grib_utils/`.

- **Data:** the GRIB files this demo downloads are [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data), licensed under CC-BY-4.0 and subject to the [ECMWF Terms of Use](https://apps.ecmwf.int/datasets/licences/general/). Data © ECMWF. They aren't part of this repository.
- **Software:** [wis2downloader](https://github.com/World-Meteorological-Organization/wis2downloader) is WMO's, installed from PyPI at build time; [earthkit](https://github.com/ecmwf/earthkit) and ecCodes are ECMWF's.
- **Diagram:** the logos in `docs/` (ECMWF, MQTT, Docker) are trademarks of their owners and are used only to identify the components.
- This is an independent demo. It isn't produced, endorsed or supported by ECMWF or WMO.

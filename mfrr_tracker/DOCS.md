# mFRR Profit Tracker

The add-on reads your Qilowatt and grid power sensors every 10 seconds and records each Kratt activation per 15-minute slot: delivered energy against the pre-signal baseline, mFRR/aFRR and Nord Pool prices, and the profit. Open it from the **mFRR** item in the sidebar.

The add-on talks to Home Assistant through the Supervisor, so you don't need a long-lived access token.

## Configuration

| Option | Description |
|---|---|
| `sensor_source` | Qilowatt source sensor. A command is active while its state is `Kratt`. |
| `sensor_mode` | Qilowatt mode sensor: `frrdown` = DOWN, `frrup` = UP. |
| `sensor_powerlimit` | Optional. Qilowatt power limit sensor (W or kW), for requested energy and delivery %. |
| `sensor_grid_power` | Grid power sensors, one per phase (W or kW, + import / − export), e.g. Shelly 3EM `channel_a/b/c_power`. They should update every few seconds. |
| `sensor_nordpool` | Nord Pool price sensor (€/kWh, no VAT or tariffs). |
| `kratt_share` | Share of activation revenue kept by Kratt (0.2 = 20 %). |
| `afrr_price_up_eur_mwh` / `afrr_price_down_eur_mwh` | aFRR price estimate used until Volton's clearing price is published. |
| `mfrr_price_area` | Bidding zone for mFRR prices from the Baltic Transparency Dashboard. |
| `mfrr_price_recheck_min` | Optional. Minutes between price checks while a finished slot waits for its price (default 5). |
| `entsoe_token` / `entsoe_cbmp_area` | Optional. ENTSO-E API token to show the aFRR market price (CBMP) for comparison. |
| `fee_network_package` | Elektrilevi network package (`vork1`, `vork2`, `vork4`, `vork5`) or `custom`. |
| `fee_*` | Optional fee defaults in cents/kWh excl. VAT (`fee_vat` in %). Fees can also be edited in the UI under **Data tools → Electricity fees**. |
| `publish_sensors` | Publish the summary sensors below to Home Assistant. |

## Sensors published to Home Assistant

With `publish_sensors` on, the add-on updates these entities every minute:

| Entity | State | Notes |
|---|---|---|
| `sensor.mfrr_net_today` | € | Net result today (spot + VAT). Attributes: the value with fees, activation income, Kratt fee, energy, slot count. |
| `sensor.mfrr_net_month` | € | The same for this month. |
| `sensor.mfrr_net_total` | € | All-time net result, `state_class: total`. |
| `sensor.mfrr_energy_today` | kWh | Delivered mFRR/aFRR energy today. |
| `sensor.mfrr_energy_total` | kWh | All-time delivered energy, `state_class: total`. |
| `sensor.mfrr_last_activation` | timestamp | Start of the latest activation. Attributes: market, direction, energy, net. |
| `sensor.mfrr_price_up` / `sensor.mfrr_price_down` | €/kWh | Price of the latest priced mFRR activation per direction. |
| `sensor.afrr_price_up` / `sensor.afrr_price_down` | €/kWh | The same for aFRR (`source: estimate` until Volton publishes). |

Notes:

- The entities are created through the Home Assistant REST API. They have no unique ID, so they can't be renamed or assigned to an area in the UI. After a Home Assistant restart they're missing until the next update, at most a minute later.
- mFRR prices are published about an hour after the slot, and they're only fetched for slots with an activation. The price sensors therefore show the latest known price, not a live market price.

### Energy dashboard and statistics

`sensor.mfrr_net_total` and `sensor.mfrr_energy_total` have a `state_class`, so Home Assistant keeps long-term statistics for them. Use them in a **Statistics graph** card with the *change* statistic to see the result per day, week or month. `sensor.mfrr_energy_total` can also be added to the Energy dashboard as an individual device.

The Energy dashboard has no place for balancing or flexibility revenue. Don't set `sensor.mfrr_net_total` as the grid return "entity tracking the total compensation": that replaces your normal export compensation and makes the energy costs wrong.

## Data

The database is stored in the add-on's `/data` folder and included in Home Assistant backups. To move data from a standalone (docker-compose) install:

1. Stop the standalone container and copy `backend/data/mffr.db` to `/share/mfrr_tracker/mffr.db` on Home Assistant, e.g. with the Samba share or SSH add-on.
2. Start the add-on, or install it now. On a start without its own database, it copies that file into `/data`.

The file is only imported once; later starts leave `/data/mffr.db` alone. Delete the copy in `/share` afterwards.

## Direct access

By default the UI is only reachable through Home Assistant (Ingress, which requires a Home Assistant login). To reach it directly, e.g. for `POST /api/qw-report` from a script, set a host port for `8000/tcp` in the **Network** section. That port has no authentication.

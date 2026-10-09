# mFRR Profit Tracker

The add-on reads your Qilowatt and grid power sensors every 10 seconds and records each Kratt activation per 15-minute slot: delivered energy against the pre-signal baseline, mFRR/aFRR and Nord Pool prices, and the profit. Open it from the **mFRR** item in the sidebar.

The add-on talks to Home Assistant through the Supervisor, so you don't need a long-lived access token.

## Configuration

All settings are add-on options; there is no second place. Edit them in either of two ways:

- Home Assistant: the add-on's **Configuration** tab, as a form or with ⋮ → **Edit in YAML**. Restart the add-on afterwards.
- The tracker's own UI: **Data tools → Configuration** shows the same options as a form, with a field for every option (also the optional ones not set yet), or as YAML. Sensor fields search your Home Assistant sensors and show their current value. **Save & restart** stores the options through the Supervisor, restarts the add-on and reloads the page when it's back. It only works when the tracker is opened from the Home Assistant sidebar, not through the direct port.

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
| `fee_*` | Optional fees in cents/kWh excl. VAT (`fee_vat` in %): `fee_margin`, `fee_taastuv`, `fee_aktsiis`, `fee_tasakaal`, `fee_varustus`, `fee_vat`, `fee_export_margin`, `fee_export_tasakaal`, and for a `custom` package `fee_elektrilevi_day`, `fee_elektrilevi_night`, `fee_elektrilevi_day_peak`, `fee_elektrilevi_holiday_peak`. A Võrk package brings its own network rates. |
| `publish_sensors` | Publish the summary sensors below to Home Assistant. |

### Sensor IDs

Home Assistant has no entity picker for add-on options, so the sensor options are entity IDs as text. In the `sensor_grid_power` list in the Configuration tab, type the full ID and choose **Add custom item**. In the tracker's **Data tools → Configuration**, start typing in a sensor field to search your sensors. Shelly entity IDs usually contain the device ID, e.g. `sensor.shellyem3_485519dbeee9_channel_a_power`; the defaults are only examples.

On start, the add-on log shows the sensors in use and any that Home Assistant doesn't have, together with the power sensors it does have. The UI shows a warning that opens the Configuration section.

## Sensors published to Home Assistant

With `publish_sensors` on, the add-on updates these entities every minute:

| Entity | State | Notes |
|---|---|---|
| `sensor.mfrr_net_today` | € | Net result today (spot + VAT). Attributes: the value with fees, activation income, Kratt fee, energy, slot count. |
| `sensor.mfrr_net_month` | € | The same for this month. |
| `sensor.mfrr_net_total` | € | All-time net result, `state_class: total`. |
| `sensor.mfrr_energy_today` | kWh | Delivered mFRR/aFRR energy today. |
| `sensor.mfrr_energy_total` | kWh | All-time delivered energy, `state_class: total`. |
| `sensor.mfrr_activation_income_total` | € | All-time activation payout: your share of the activation revenue after Kratt's fee, without the bill effect. Uses the official Qilowatt figures for slots covered by an imported revenue report (attributes `official_slots` / `estimated_slots`). |
| `sensor.mfrr_energy_dashboard_zero` | kWh | Always 0. Helper from version 1.1.0; use the statistics below for the Energy dashboard instead. |
| `sensor.mfrr_last_activation` | timestamp | Start of the latest activation. Attributes: market, direction, energy, net. |
| `sensor.mfrr_price_up` / `sensor.mfrr_price_down` | €/kWh | Price of the latest priced mFRR activation per direction. |
| `sensor.afrr_price_up` / `sensor.afrr_price_down` | €/kWh | The same for aFRR (`source: estimate` until Volton publishes). |

Notes:

- The entities are created through the Home Assistant REST API. They have no unique ID, so they can't be renamed or assigned to an area in the UI. After a Home Assistant restart they're missing until the next update, at most a minute later.
- mFRR prices are published about an hour after the slot, and they're only fetched for slots with an activation. The price sensors therefore show the latest known price, not a live market price.

### Energy dashboard and statistics

`sensor.mfrr_net_total`, `sensor.mfrr_energy_total` and `sensor.mfrr_activation_income_total` have a `state_class`, so Home Assistant keeps long-term statistics for them. Use them in a **Statistics graph** card with the *change* statistic to see the result per day, week or month. `sensor.mfrr_energy_total` can also be added to the Energy dashboard as an individual device.

The Energy dashboard has no place for balancing income, but the Kratt payout can be added to its costs as an extra grid return that has no energy. For this, the add-on writes two hourly statistics to Home Assistant itself (they're listed in **Developer Tools → Statistics**):

| Statistic | Contents |
|---|---|
| `mfrr_tracker:activation_payout` (**mFRR activation payout**) | Your activation payout per hour, € |
| `mfrr_tracker:energy_zero` (**mFRR zero energy**) | Always 0 kWh |

Setup:

1. **Settings → Dashboards → Energy → Electricity grid → Add return.**
2. Energy: **mFRR zero energy**. The energy totals and flows don't change.
3. Compensation: **Use an entity tracking the total compensation**, then pick **mFRR activation payout**.

The grid cost then includes the activation payout. Only the payout is added, because the rest of an activation's result, the extra import or export, is already measured by your grid meter and priced by the Energy dashboard. Don't use `sensor.mfrr_net_total` here, because that would count the bill effect twice. Don't attach anything to your real grid return sensor either: that replaces your normal export compensation.

**Estimate first, actual later.** Each 15-minute slot is booked in the hour it belongs to, and is corrected in place when better figures arrive:

- the mFRR price from the Baltic Transparency Dashboard, about an hour after the slot
- Volton's aFRR clearing price, which replaces the 440 / −530 €/MWh estimate
- a backfill, which recomputes past rows
- an imported **Qilowatt balancing revenue report**: for every slot it covers, the official revenue share replaces the tracker's estimate. Upload the CSV under **Data tools** whenever you get a report (weekly, monthly), or post it to `/api/qw-report`.

The statistics are updated every 5 minutes, and right after a report import. Past hours in the Energy dashboard then show the corrected figures. Until a figure is known, the hour contains the tracker's estimate, or nothing if the price isn't published yet.

Notes:

- Home Assistant's currency (Settings → System → General) must be EUR.
- If you set this up with version 1.1.0 (`sensor.mfrr_energy_dashboard_zero` and `sensor.mfrr_activation_income_total`), switch the return to the two statistics above and delete the old one. The sensor books every correction in the hour it happens instead of the hour it belongs to.

## Data

The database is stored in the add-on's `/data` folder and included in Home Assistant backups. To move data from a standalone (docker-compose) install:

1. Stop the standalone container and copy `backend/data/mffr.db` to `/share/mfrr_tracker/mffr.db` on Home Assistant, e.g. with the Samba share or SSH add-on.
2. Start the add-on, or install it now. On a start without its own database, it copies that file into `/data`.

The file is only imported once; later starts leave `/data/mffr.db` alone. Delete the copy in `/share` afterwards.

## Direct access

By default the UI is only reachable through Home Assistant (Ingress, which requires a Home Assistant login). To reach it directly, e.g. for `POST /api/qw-report` from a script, set a host port for `8000/tcp` in the **Network** section. That port has no authentication.

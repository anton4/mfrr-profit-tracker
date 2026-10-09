# Changelog

## 1.3.1

- The UI shows the new version right after an add-on update. Before, the browser could keep using the cached page from the previous version for a while.
- The log shows the add-on version on start, and whether an ENTSO-E token is set.

## 1.3.0

- Pick the sensors in the tracker's UI under **Data tools → Sensors**. It lists your Home Assistant sensors with their current values and shows the summed grid power. Sensors saved there override the add-on options. The add-on options can't list entities, because Home Assistant has no entity picker for add-ons.
- On start, the add-on logs the sensors in use and any that Home Assistant doesn't have, together with the power sensors it does have. A sensor that can't be read is logged once, and again when it recovers, instead of every 10 seconds. A warning in the UI links to the Sensors section.
- Every log line starts with the date and time.

## 1.2.0

- Write the activation payout to Home Assistant as hourly statistics (`mfrr_tracker:activation_payout`, with `mfrr_tracker:energy_zero` for the Energy dashboard). Later corrections, such as published prices, backfills and imported Qilowatt revenue reports, now rewrite the hours they belong to instead of landing in the current hour.
- Use the official Qilowatt revenue share for slots covered by an imported revenue report, in the statistics and in `sensor.mfrr_activation_income_total`.
- If you set up the Energy dashboard with 1.1.0, switch to the new statistics (see the Documentation tab).

## 1.1.0

- Add `sensor.mfrr_activation_income_total` and the `sensor.mfrr_energy_dashboard_zero` helper, so the Kratt activation payout can be added to the Energy dashboard's costs as a grid return without changing any energy figures. See the Documentation tab.

## 1.0.0

- First release as a Home Assistant add-on: Ingress sidebar panel, configuration in the add-on options, and the Home Assistant API token provided by the Supervisor.
- Publishes summary sensors to Home Assistant: net result today / this month / total, delivered energy, last activation and the latest mFRR and aFRR prices.

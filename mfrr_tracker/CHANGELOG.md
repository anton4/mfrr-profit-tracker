# Changelog

## 1.2.0

- Write the activation payout to Home Assistant as hourly statistics (`mfrr_tracker:activation_payout`, with `mfrr_tracker:energy_zero` for the Energy dashboard). Later corrections, such as published prices, backfills and imported Qilowatt revenue reports, now rewrite the hours they belong to instead of landing in the current hour.
- Use the official Qilowatt revenue share for slots covered by an imported revenue report, in the statistics and in `sensor.mfrr_activation_income_total`.
- If you set up the Energy dashboard with 1.1.0, switch to the new statistics (see the Documentation tab).

## 1.1.0

- Add `sensor.mfrr_activation_income_total` and the `sensor.mfrr_energy_dashboard_zero` helper, so the Kratt activation payout can be added to the Energy dashboard's costs as a grid return without changing any energy figures. See the Documentation tab.

## 1.0.0

- First release as a Home Assistant add-on: Ingress sidebar panel, configuration in the add-on options, and the Home Assistant API token provided by the Supervisor.
- Publishes summary sensors to Home Assistant: net result today / this month / total, delivered energy, last activation and the latest mFRR and aFRR prices.

# Changelog

## 1.5.0

- **Kratt right now** at the top of the page: whether a command is active, UP or DOWN, mFRR or aFRR, since when, the requested and delivered power, the grid power and the baseline. Next to it, a chart of the last two hours: grid power, Kratt's target (baseline ± requested) and the commands as bands. Hover or use the arrow keys for the values at a moment; the table view lists them per minute. After a restart, the chart is filled from Home Assistant's history.
- A restart during an activation no longer sets its baseline to 0 W: the baseline is taken from the history before the command.
- Changed prices and fees (Kratt share, aFRR estimates, electricity fees and network package) apply either **from now on** or to **all history**. **Data tools → Configuration** asks when you save; a change in Home Assistant's Configuration tab applies from now on.

## 1.4.1

- **Data tools → Configuration** has two views again: a form with a field for every option, and the raw YAML. Switch between them at any time; both save the same add-on options.
- The form shows all options, also the optional ones that aren't set yet, such as the ENTSO-E token, with their defaults as placeholders. Sensor fields search your Home Assistant sensors and show their current value; the grid power sensors are one row per phase with the summed grid power.
- The network package is a choice of Võrk 1, 2, 4, 5 or Custom again, showing the package's network rates; the network rate fields appear for Custom.
- Home Assistant's own Configuration tab now shows a name and description for each option.

## 1.4.0

- All settings live in one place: the add-on options. **Data tools → Configuration** in the tracker edits them as YAML, with a search to insert sensor IDs, and **Save & restart** applies them. The separate Sensors and Electricity fees editors in the tracker are gone.
- Sensors and fees you saved in the tracker's UI are moved into the add-on options on the first start, and the log lists what was moved.
- New options `fee_elektrilevi_day_peak` and `fee_elektrilevi_holiday_peak` for a custom network package with peak hours.

## 1.3.2

- The page header shows the version of the page you're looking at. If the browser still shows a page cached from an older version, a **Version … is installed** notice appears; click it to load the current page.

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

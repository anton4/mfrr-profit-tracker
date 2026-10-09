# Changelog

## 1.7.0

- The period at the top and the graph are one window. Picking Today, Yesterday, This week, … shows that period in the graph (live while it reaches now; All shows the last 31 days), and zooming or moving the graph sets the period to Custom with the graph's window, so the figures and tables follow the graph.
- The page opens on Today in the graph too, from midnight to now, instead of the last 2 hours.
- **From** and **To** show the period's window at all times and take a date and time; an empty To means until now. A custom period is kept in the address bar, so a reload or a shared link opens it again.
- A custom period counts the slot it starts in.
- **Show in graph** on an activation no longer changes the period; **← Back to …** under the graph returns to it.
- The strip under the graph reaches back to where the graph starts when that is more than 7 days ago.
- The price status lines are shown one per line.

## 1.6.2

- aFRR commands are striped on the graph, mFRR commands solid, in the same red (DOWN) and green (UP). Wide bands say which, e.g. "UP · aFRR", and so does the tooltip.
- Click an entry in the graph's legend to hide or show it: grid power, Kratt's target, DOWN, UP, mFRR or aFRR. The axis fits what's shown, and the choice is remembered in the browser.

## 1.6.1

- The graph zooms with the scroll wheel, no Ctrl/⌘ needed. Dragging across it selects a range and zooms into it on release, with the selected times shown while you drag (Esc cancels). To move the graph, Shift-drag or scroll sideways; on a touch screen, drag with one finger.

## 1.6.0

- The graph shows any period, not only the last two hours: presets from 15 minutes to 7 days, ◀ ▶ to step back and forward, Live to follow now, Go to a date and time, and a 7-day strip to click or drag through. Drag to move, Shift-drag to zoom into a range, Ctrl/⌘ + scroll or pinch to zoom, double-click to zoom out.
- **Show in graph** on each activation opens the graph around it.
- The data comes from Home Assistant, nothing is stored twice: up to 6 hours from the recorded history with 10-second detail, the baseline and Kratt's target; longer periods from Home Assistant's long-term statistics (5-minute or hourly means).

## 1.5.1

- The price status shows the three sources the same way: **mFRR prices** (Baltic Transparency Dashboard), **aFRR prices** (Volton) and **aFRR market price** (ENTSO-E). Each says whether it's up to date, waiting, failing or off, when it was checked, when it's checked next and how many slots wait for it. The separate Waiting and Error chips are gone.
- The aFRR checks run right after a start. Before, the first ENTSO-E check was skipped when the start took more than a second, and Volton waited 5 minutes.
- The Backfill panel says that a backfill keeps running when you leave the page.

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

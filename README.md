# PageCrawl for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/pagecrawl/hass-pagecrawl?style=for-the-badge)](https://github.com/pagecrawl/hass-pagecrawl/releases)
[![Validate](https://img.shields.io/github/actions/workflow/status/pagecrawl/hass-pagecrawl/validate.yml?branch=main&style=for-the-badge&label=validate)](https://github.com/pagecrawl/hass-pagecrawl/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/pagecrawl/hass-pagecrawl?style=for-the-badge)](LICENSE)

Bring your [PageCrawl.io](https://pagecrawl.io) website monitors into Home Assistant as
sensors you can view, chart, and automate on. Every monitor updates in real time the moment
PageCrawl detects a change.

### Great for (what `rest` and `scrape` sensors can't do)

Home Assistant already fetches URLs and scrapes CSS selectors on simple, static pages. Reach
for PageCrawl when those fall short:

- **Sites that block ordinary scrapers:** pages that reject automated requests, or sit behind
  a login, return nothing to a plain fetch. PageCrawl reliably reads them, so retail, ticketing,
  and other guarded pages keep working as Home Assistant sensors.
- **JavaScript-rendered pages:** prices, stock, and dashboards that load dynamically come back
  empty from a plain fetch or scrape. PageCrawl loads the page fully before reading it, so you
  still get a value.
- **AI extraction instead of brittle selectors:** describe the value in plain language ("the
  next collection date", "the current service status") and it keeps working even when the page
  layout changes and a CSS selector would break.
- **Change detection without false positives:** a raw `scrape` sensor fires on every rotating
  ad, timestamp, or reordered block. PageCrawl's check pipeline filters that noise out, so you
  are alerted only on changes that matter, with a human-readable summary of what changed.
- **Visual change detection:** know when a page changes visually, backed by screenshots, not
  only when a text value moves.

### Which one should you use?

Home Assistant's built-in `rest` and `scrape` sensors are a great fit for many pages, and they
run entirely locally. Reach for PageCrawl only when they fall short:

| Use a built-in `rest` / `scrape` sensor when | Use PageCrawl when |
|---|---|
| The page is static, public HTML or a JSON API | The page needs JavaScript to render the value |
| A stable CSS selector or JSON path exists | No reliable selector, or it breaks when the page changes |
| The page has no login and allows automated requests | The page needs a login or blocks ordinary scrapers |
| You only need the current value | You want change history, diffs, or a human/AI summary of what changed |
| Any change to the value is meaningful | The page is noisy (ads, timestamps, reordered blocks) and you only want real changes |
| You are happy maintaining the selector yourself | You want AI extraction and no scraping logic to maintain |

A good rule of thumb: if a `scrape` sensor already returns the value you need, keep using it.
Bring in PageCrawl for the pages where it comes back empty, gets blocked, or needs constant
selector fixes.

This is a custom integration (a HACS-installable custom component), not an add-on. Sensors
in Home Assistant can only be created by an integration, so this works on every install
type: Core, Container, OS, and Supervised.

## What you get

- One Home Assistant device per monitor.
- One entity per tracked element on that monitor, typed correctly (numeric, on/off, text,
  or item counts).
- Real-time push updates when a change is detected, with polling as a fallback.
- A "Check now" button on every monitor, plus `pagecrawl.check_now` and
  `pagecrawl.track_page` services.
- A `pagecrawl_change` event for automations.
- A choice of what to import (everything, selected folders, or selected monitors), and
  support for multiple workspaces.

It works for **free PageCrawl accounts**. You sign in with OAuth and click Authorize. There
is no API token to create or paste.

## Installation

### HACS (recommended)

This integration is installed through [HACS](https://hacs.xyz). If you do not have HACS yet,
[install it first](https://hacs.xyz/docs/use/download/download/).

Click the button below to open this repository in HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pagecrawl&repository=hass-pagecrawl&category=integration)

Then select **Download**, and **restart Home Assistant**.

<details>
<summary>Or add it manually as a custom repository</summary>

1. In Home Assistant, open **HACS**.
2. Open the menu (top right) and choose **Custom repositories**.
3. Add the repository URL `https://github.com/pagecrawl/hass-pagecrawl` with category
   **Integration**, then add it.
4. Find **PageCrawl** in HACS, select **Download**, and restart Home Assistant.

</details>

### Manual

1. Download the latest release from the
   [releases page](https://github.com/pagecrawl/hass-pagecrawl/releases).
2. Copy `custom_components/pagecrawl` into your Home Assistant `config/custom_components`
   directory.
3. Restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=pagecrawl)

1. Click the button above, or go to **Settings > Devices & Services > Add Integration** and
   search for **PageCrawl**.
2. You are redirected to PageCrawl to sign in and authorize Home Assistant. This is a
   one-click PKCE OAuth flow: there is no API token to create or paste, and a free account
   is enough. The integration uses a built-in public OAuth client, so nothing is configured
   by hand on pagecrawl.io.
3. If your account has more than one workspace, pick the one to add. To add another
   workspace later, run **Add Integration** again and pick a different workspace. Each
   workspace becomes its own entry with its own devices and entities.

The OAuth client is built in, so there is nothing to configure by hand: no API token, no
client ID or secret, and no Application Credentials to add.

## What this integration can and cannot do

The token Home Assistant receives is least-privilege. It is granted a read and trigger
scope, not full account access. Concretely, an integration token can:

- Read your monitors and their tracked elements (list and show).
- Trigger an immediate check of a monitor ("Check now").
- Create a new monitor (the `pagecrawl.track_page` service).
- Manage its own push webhook on your account (so push delivery can be set up
  automatically).

It deliberately cannot:

- Edit a monitor's settings.
- Delete a monitor.
- Reach the rest of your account's write operations (settings, billing, bulk edits, and so
  on).

Editing and deleting monitors stay in the PageCrawl web app. This keeps free OAuth access
safe: even if the token leaked, it could only read, check, and add monitors, never remove
or reconfigure them.

## Update modes

The update mode is set in the integration's **Configure** (options) screen.

- **Auto (default)**: uses push when Home Assistant has a reachable URL (a Home Assistant
  Cloud / Nabu Casa subscription provides one automatically), otherwise falls back to
  polling. The integration tells you which mode is active.
- **Push and poll**: forces push, with a slow reconciliation poll. Needs a reachable URL.
- **Polling only**: never registers a webhook and checks on the schedule you set with the
  poll interval. Use this for local-only installs that cannot expose an endpoint.

Push needs a URL that PageCrawl can reach from the internet. A **Home Assistant Cloud
cloudhook** is the recommended way to get one. If no reachable URL is available, the
integration raises a repair notice and uses polling.

The poll interval has a 60 second minimum to respect PageCrawl rate limits. With push
enabled, the poll is only a slow reconciliation loop to catch any missed deliveries.

## Choosing what to import

During setup you pick how much of the workspace to bring into Home Assistant:

- **All monitors (default)**: every monitor in the workspace becomes a device.
- **Selected folders**: only monitors in the folders you choose are imported.
- **Selected monitors**: you hand-pick the exact monitors to import.

You can change this later in the integration's **Configure** (options) screen. If you
narrow the selection, the devices and entities for the de-selected monitors are removed
automatically. Widening it again imports the newly in-scope monitors on the next update.

## Multiple workspaces

PageCrawl scopes everything to the current workspace. To monitor more than one workspace,
add the integration once per workspace. The OAuth login is shared, and each entry pins its
own workspace, so devices, entities, and the push webhook stay isolated per workspace.

## Entity and element mapping

Each monitor becomes a device. Each tracked element becomes one entity, chosen by its type:

| Element type | Entity | State |
|---|---|---|
| `price` | sensor (monetary) | numeric value |
| `number` | sensor (measurement) | numeric value |
| `rating` | sensor (measurement) | numeric value |
| `reviews` | sensor (measurement) | numeric value |
| `http_status` | sensor | numeric status code |
| `boolean` | binary sensor | on when truthy |
| `availability` | binary sensor | on when in stock |
| `text`, `fullpage`, `html`, `ai_extract`, `json_path`, `seo` | sensor | text, truncated to 255 characters (full value in the `full_value` attribute) |
| `links`, `feed`, `leaderboard`, `text_multiple` | sensor | item count (items in the `items` attribute) |

Every monitor also gets diagnostic entities (status, last checked, last change date, change
percent) so the device is never empty, even if its element types are unknown. Common attributes such as the
URL, status, change percent, and diff and screenshot links are exposed on the primary
sensor.

Each monitor also gets a few per-monitor sensors that describe its latest change:

- **Last change**: a short, human-readable summary of what changed at the last check (full
  text in the `full_value` attribute).
- **AI summary**: the AI summary of the latest change. It appears only when AI analysis is
  enabled on that monitor.
- **AI priority**: a diagnostic 0-100 score for how important the latest change is. It
  appears only when AI analysis is enabled on that monitor.

When a monitor tracks several elements, push updates carry the changed element's id, so each
element's entity updates the instant its own value changes, not just the primary one. The
reconciliation poll catches up any element that a missed delivery would have skipped.

## Services

### `pagecrawl.check_now`

Trigger an immediate check of one or more monitors, then refresh their entities. Target any
entity or device that belongs to the monitor, or name the monitor directly by `slug` or
`monitor_id` (no need to look up the Home Assistant device id):

```yaml
service: pagecrawl.check_now
data:
  slug: openai-about
```

```yaml
service: pagecrawl.check_now
target:
  device_id: 1a2b3c4d5e6f7g8h9i0j
```

### `pagecrawl.track_page`

Create a new monitor. Its device and entities appear after the next refresh.

```yaml
service: pagecrawl.track_page
data:
  url: https://example.com/product/widget
  name: Widget price
  tracking_mode: price
```

For AI extraction:

```yaml
service: pagecrawl.track_page
data:
  url: https://example.com/events
  name: Next event date
  tracking_mode: ai_extract
  prompt: Extract the date of the next event in ISO format.
```

If you have more than one workspace set up, add `entry_id` to choose which one the monitor is
created in.

## Automations

The integration fires a `pagecrawl_change` event whenever a monitor's latest change advances.

```yaml
alias: Notify on PageCrawl change
trigger:
  - platform: event
    event_type: pagecrawl_change
action:
  - service: notify.notify
    data:
      title: "PageCrawl: {{ trigger.event.data.name }}"
      message: >-
        {{ trigger.event.data.human_difference }}
        {{ trigger.event.data.diff_url }}
```

Event data includes `monitor_id`, `name`, `url`, `slug`, `status`, `contents`, `difference`,
`human_difference`, `ai_summary`, `ai_priority_score`, `diff_url`, and `changed_at`. The
`ai_summary` and `ai_priority_score` fields are present when AI analysis is enabled on the
monitor, so you can filter and route changes straight from the event, with no per-monitor
sensor lookups.

### More examples

These lean into the two things PageCrawl adds to Home Assistant: it reads values from pages
that a plain `rest` or `scrape` sensor comes back empty on (JavaScript-rendered, login-gated,
or bot-blocked), and it tells you what actually changed, with an AI summary and priority, so
you can act on the physical world instead of just sending another notification.

**Actionable mobile alert, tap to open the diff.** The whole change rides on the event, so one
automation covers every monitor. The notification opens the diff when tapped:

```yaml
alias: PageCrawl change to my phone
trigger:
  - platform: event
    event_type: pagecrawl_change
action:
  - service: notify.mobile_app_phone
    data:
      title: "Changed: {{ trigger.event.data.name }}"
      message: >-
        {{ trigger.event.data.ai_summary or trigger.event.data.human_difference }}
      data:
        url: "{{ trigger.event.data.diff_url }}"
        clickAction: "{{ trigger.event.data.diff_url }}"
```

**Only the changes worth interrupting you for.** PageCrawl scores how important each change is,
so you can drop the noise. Filter on the score carried by the event, not a per-monitor sensor,
so it stays correct no matter which monitor fired:

```yaml
alias: High-priority PageCrawl changes only
trigger:
  - platform: event
    event_type: pagecrawl_change
condition:
  - condition: template
    value_template: "{{ (trigger.event.data.ai_priority_score | int(0)) >= 70 }}"
action:
  - service: notify.notify
    data:
      title: "Important: {{ trigger.event.data.name }}"
      message: "{{ trigger.event.data.ai_summary }} {{ trigger.event.data.diff_url }}"
```

**Back in stock: open the garage light and say it out loud.** Availability monitors are binary
sensors, so the moment an item flips to in stock you can do something physical and announce it:

```yaml
alias: PS5 back in stock
trigger:
  - platform: state
    entity_id: binary_sensor.ps5_availability
    from: "off"
    to: "on"
action:
  - service: light.turn_on
    target:
      entity_id: light.office
    data:
      flash: short
  - service: tts.speak
    data:
      cache: false
      media_player_entity_id: media_player.kitchen
      message: "Heads up, the PlayStation 5 is back in stock."
    target:
      entity_id: tts.home_assistant_cloud
```

**Charge the car when power is cheap.** Your dynamic energy tariff is a JavaScript dashboard
that Home Assistant cannot scrape on its own. PageCrawl reads the live price as a number sensor,
so you can let the house act on a value it otherwise could not see:

```yaml
alias: Charge EV on cheap power
trigger:
  - platform: numeric_state
    entity_id: sensor.grid_price_per_kwh
    below: 0.12
action:
  - service: switch.turn_on
    target:
      entity_id: switch.ev_charger
```

**Notify when an appointment slot opens.** PageCrawl monitors a login-gated booking page and
exposes the number of open slots as a count sensor. Fire the moment it goes above zero:

```yaml
alias: Appointment slots available
trigger:
  - platform: numeric_state
    entity_id: sensor.passport_office_slots
    above: 0
action:
  - service: notify.mobile_app_phone
    data:
      title: "Slots open: {{ states('sensor.passport_office_slots') }}"
      message: "Book now: {{ state_attr('sensor.passport_office_slots', 'url') }}"
```

**Recheck a monitor on a schedule by name.** Target the monitor by its `slug`, so you never
have to dig up a Home Assistant device id:

```yaml
alias: Hourly recheck of the status page
trigger:
  - platform: time_pattern
    minutes: "0"
action:
  - service: pagecrawl.check_now
    data:
      slug: your-monitor-slug
```

Replace the example entity ids and slugs with your own (Home Assistant builds entity ids from
the monitor name, and the slug is the one in your monitor's pagecrawl.io URL).

## What is not supported yet

Editing and deleting monitors from Home Assistant is intentionally not supported for now.
The integration can create monitors (with `pagecrawl.track_page`) and read and check them,
but changes and removals are managed in the PageCrawl web app. Screenshot and visual diff
images are exposed as attribute URLs; native image entities may come later.

## Notes

- This integration is fully asynchronous and adds no extra Python dependencies.
- hassfest and HACS validation run on every push via GitHub Actions. Submission to the
  default HACS store and the [home-assistant/brands](https://github.com/home-assistant/brands)
  icon listing are planned follow-ups.

## License

See [LICENSE](LICENSE).

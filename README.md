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

- **JavaScript-rendered pages** — prices, stock, and dashboards that load dynamically come
  back empty from a plain fetch or scrape. PageCrawl loads the page fully before reading it,
  so you still get a value.
- **Sites a basic scraper can't reach** — pages behind a login, or that block automated
  requests, keep working as reliable sensors.
- **AI extraction instead of brittle selectors** — describe the value in plain language ("the
  next collection date", "the current service status") and it keeps working even when the
  page layout changes and a CSS selector would break.
- **Meaningful-change detection** — PageCrawl tracks history and tells you what actually
  changed, with a human-readable summary, instead of you polling a raw value and writing your
  own diff logic.
- **Visual change detection** — know when a page changes visually, backed by screenshots, not
  only when a text value moves.

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
- Optional folder and tag filtering, and support for multiple workspaces.

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

Every monitor also gets diagnostic entities (status, last checked, change percent) so the
device is never empty, even if its element types are unknown. Common attributes such as the
URL, status, change percent, and diff and screenshot links are exposed on the primary
sensor.

When a monitor tracks several elements, push updates carry the changed element's id, so each
element's entity updates the instant its own value changes, not just the primary one. The
reconciliation poll catches up any element that a missed delivery would have skipped.

## Services

### `pagecrawl.check_now`

Trigger an immediate check of one or more monitors, then refresh their entities. Target any
entity or device that belongs to the monitor.

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

Event data includes `monitor_id`, `name`, `url`, `slug`, `contents`, `difference`,
`human_difference`, `diff_url`, and `changed_at`.

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

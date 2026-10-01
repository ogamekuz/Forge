# Setting up Forge — step-by-step guide

**English** · [Русский](SETUP.ru.md) · [← README](../README.md)

This guide takes you from the unpacked archive to your first accurate calculation: SDE,
characters, your locations (where you buy, build and sell), stations with rigs, freight,
character roles, blueprints and stock. Buttons and fields are named exactly as they appear in the
program with the **ENG** language selected. Every tab and option is described in the
[README](../README.md#the-program-tab-by-tab).

The first setup takes 20–40 minutes, most of which is the program downloading data by itself.

**Contents**

1. [What you need](#1-what-you-need)
2. [Install and launch](#2-install-and-launch)
3. [Static game data (SDE)](#3-static-game-data-sde)
4. [Characters (EVE SSO)](#4-characters-eve-sso)
5. [Syncing data](#5-syncing-data)
6. [Locations: where you buy, build and sell](#6-locations-where-you-buy-build-and-sell)
7. [Stations and rigs](#7-stations-and-rigs)
8. [Stock structures and market structures](#8-stock-structures-and-market-structures)
9. [Taxes, market and freight](#9-taxes-market-and-freight)
10. [Character roles and slot limits](#10-character-roles-and-slot-limits)
11. [Blueprints](#11-blueprints)
12. [Stock](#12-stock)
13. [Invention, always buy / build, planner](#13-invention-always-buy--build-planner)
14. [Auto-update](#14-auto-update)
15. [Check: your first calculation](#15-check-your-first-calculation)
16. [Updating, moving and backups](#16-updating-moving-and-backups)
17. [Troubleshooting](#17-troubleshooting)
18. [Editing forge.toml by hand](#18-editing-forgetoml-by-hand)

---

## 1. What you need

- Windows 10 or 11 and about 1 GB of disk space (game data and your own data).
- Access to the EVE accounts whose characters you want to add. You log in on the official EVE
  Online page; you never type your password into Forge.
- Decide in advance (you can change it later):
  - **where you buy** materials (usually Jita);
  - **where you sell** products: in the same hub or on a market structure (an alliance
    Keepstar, etc.);
  - **where you build**: the system with your (or accessible) Engineering Complexes and
    Refineries;
  - what delivery between these places costs.

You don't need to know structure IDs in advance: Forge shows them after a character sync
([section 8](#8-stock-structures-and-market-structures)).

## 2. Install and launch

### Portable build (recommended)

1. Download `Forge_<version>.zip` from [Releases](https://github.com/ogamekuz/Forge/releases).
2. Unpack the **whole** archive into any folder you can write to: Desktop, Documents,
   `D:\Games\Forge`… No administrator rights needed. Don't run it from inside the archive: without
   unpacking it can't find its bundled Python.
3. Double-click `Forge.cmd`.
4. If Windows shows "Windows protected your PC", click "More info", then "Run anyway". The program
   is not code-signed, which is all the warning is about.
5. If the firewall asks about `pythonw.exe`, allow it. That's the built-in report server; it
   listens on `127.0.0.1` only and is not reachable from outside.

The portable build already contains an EVE SSO application (Client ID); you don't need to register
your own.

### From source

Details are in the [README](../README.md#from-source). In short: Python 3.12+, `pip install -e
".[dev,desktop]"`, `copy forge.example.toml forge.toml`. Then register your own application at
<https://developers.eveonline.com/>:

- *Connection type* — **Authentication & API Access**;
- *Callback URL* — `http://localhost:8765/callback`;
- *Scopes* — the seven scopes from the table in [section 4](#4-characters-eve-sso).

Copy the application's **Client ID** into "Settings → System → EVE SSO" and click **Save**. No
Secret Key is needed: Forge logs in with OAuth2 PKCE.

### Language

The **RUS / ENG** switch is in the window header. The language changes immediately; new HTML
reports are written in the selected language.

## 3. Static game data (SDE)

The SDE is the game's reference data: items, blueprints, rigs, systems. Without it neither search
nor calculations work.

1. **Overview** tab → **Sync** card → **Download SDE** → **Download**.
2. Wait for "Sync complete.". It takes a few minutes: the Fuzzwork dump is hundreds of megabytes.
3. Progress and results are shown in the **Data sources** card ("SDE (Fuzzwork)" row).

Repeat only after major game patches (new items, blueprints, rigs).

## 4. Characters (EVE SSO)

Forge sees slots, skills, blueprints, assets and running jobs only for the characters you add.
Jobs keep running while you're offline, so it makes sense to add **all** your industry characters
from all accounts.

### Adding a character

1. **Overview → Add character (EVE SSO)**. Your default browser opens the official EVE Online
   login page (`login.eveonline.com`).
2. Log in and **select the character** from the list.
3. Review the permissions (all read-only, see the table below) and click **Authorize**.
4. The browser shows "Forge: authorization received.". You can close the tab.
5. Forge shows "Added: <name>".
6. Repeat for each character: one login per character.

Tips:

- **A character from another account.** If the EVE page immediately offers the characters of the
  account you're already logged into, log out of that account on the page (switch account) and log
  in with the one you need.
- **You have 5 minutes to log in.** If you run out of time, just click the button again.
- Logging in again with the same character is safe: its record and access are simply refreshed.

### What permissions Forge asks for

All scopes are read-only: Forge changes nothing in the game and never moves ISK.

| Scope | What for |
|---|---|
| `esi-characters.read_blueprints.v1` | your blueprints: ME/TE, runs, where they are |
| `esi-skills.read_skills.v1` | skills: slot counts, job time, invention chance |
| `esi-assets.read_assets.v1` | assets: stock and blueprint locations |
| `esi-industry.read_character_jobs.v1` | running jobs: busy slots in the schedule |
| `esi-wallet.read_character_wallet.v1` | wallet balance (Overview and Characters cards) |
| `esi-markets.structure_markets.v1` | market structure orders (sales market prices) |
| `esi-universe.read_structures.v1` | structure names and systems (stock, stations) |

### Where access is stored

Forge doesn't store your password; it stores an EVE refresh token. Tokens live in the **Windows
Credential Manager**, not in the database and not in `forge.toml`. If EVE stops accepting a saved
login (password change, access revoked on the EVE website, character transfer), the Overview and
the Characters tab show "login needed". Then use **Add character (EVE SSO)** with that character
again.

## 5. Syncing data

The **Sync** card on the Overview is the only place where Forge goes online.

1. **Characters + structure markets** — blueprints, skills, assets, jobs and wallets of all
   characters; structure names and systems; orders of the market structures from the settings.
   Takes from a few seconds to a couple of minutes.
2. **Jita market & indices** — purchasing hub prices, adjusted prices (the basis of job cost),
   system cost indices, market history. The first run takes up to 10–15 minutes (history
   download); after that it's faster, history refreshes once a day.

Check: the **Characters** tab — every character has slots, blueprints, assets and an update time.
The sync respects the ESI cache, so running it again right after the previous one may fetch
nothing new — that's normal.

> It pays to sync characters and the market **after** setting up locations and structures
> (sections 6–8): that's when structure names and market structure orders are fetched.

## 6. Locations: where you buy, build and sell

**Settings → Logistics & market → Locations.** This is the most important setting: purchase
prices, sale prices, the job cost index and freight all depend on it.

```
 Purchasing hub ──freight──▶ Sales market / 2nd hub ──freight──▶ Build location ──freight──▶ Sales market
 (usually Jita)              (you can buy here too)              (your EC & Refinery)        (selling)
```

| Role | What it is | Where prices come from |
|---|---|---|
| **Purchasing hub** | where you buy materials | market aggregates for the hub's region (Fuzzwork) |
| **Sales market / 2nd hub** | where you sell products (and can also buy) | orders of the market structures from the settings; if the sales market is in the same region as the purchasing hub, the hub's prices |
| **Build location** | the system where jobs run | this system's cost index (unless a station has its own system) |

To change a location:

1. In the role's row click **"change system…"**, start typing the system name and pick it from
   the list. The region is filled in automatically.
2. Type the **name** you want to see in reports into the field on the left. The name is filled in
   automatically only if the field is empty — **the old name is not replaced when you change the
   system**, so fix it by hand.
3. **Save** (button at the top of the settings page).

Typical setups:

| Your case | Purchasing hub | Sales market / 2nd hub | Build location | Market structures |
|---|---|---|---|---|
| High-sec: buy and sell in Jita | Jita | **Jita** | your system | not needed |
| Null-sec: buy in Jita, sell on a Keepstar | Jita | the Keepstar's system | the system of your EC/Refinery | the Keepstar (and other markets in that system) |
| Build and sell in one system with a market | Jita | the build system | the build system | that system's market structure |

> ⚠ **Sale prices.** Forge takes sales market prices from **market structure** orders
> ([section 8](#8-stock-structures-and-market-structures)), or, if the sales market is in the same
> region as the purchasing hub, from the hub's prices. Forge does not download an NPC hub in
> another region (Amarr, Dodixie, Rens, Hek) as a sales market. If products in the Calculator have
> no sell price and no profit, this is almost always the cause: set the sales market to the
> purchasing hub's system (Jita) or add a market structure.

Without your own stations Forge still calculates — just without structure bonuses and with the
build system's cost index.

## 7. Stations and rigs

**Settings → Stations.** Here you describe the structures where you run jobs: their type, rigs,
tax and which products go there. This drives material use (ME bonus), job time and installation
cost.

### How Forge picks a station for a job

1. The **job type** determines the role: reaction → "reactions", invention → "invention",
   copying → "copying", manufacturing → "manufacturing".
2. For manufacturing, Forge first looks for a **"T2 components (by group)"** station whose list
   contains the product's group or category.
3. Then a station of the job's role whose "Which products go here" contains the **product's group
   or category**.
4. Then a station of the job's role with an **empty** "Which products go here" — "for everything
   else".
5. If no station fits, the fallback multipliers from "Settings → Manufacturing" are used (no
   bonuses by default).

A station with groups or categories always beats a station "for everything". Card order matters
only when two cards of the same role claim the same product: then the upper one wins.

### Roles

| Forge role | Which jobs | Typical structure |
|---|---|---|
| manufacturing (everything else) | all manufacturing not claimed by other stations | Raitaru / Azbel / Sotiyo |
| T2 components (by group) | manufacturing of the listed groups only (groups are required) | EC with Advanced Component rigs |
| reactions | reactions | Athanor / Tatara |
| invention | T2/T3 invention | EC with Invention rigs |
| copying | blueprint copying (for invention) | Engineering Complex |

Rig size must fit the structure: **M-Set** — Raitaru and Athanor, **L-Set** — Azbel and Tatara,
**XL-Set** — Sotiyo.

### Adding a station

1. **Add station**. A "New station" card appears.
2. **Name** — anything, for you and the reports. For example: "Sotiyo — ships".
3. **Role** — the drop-down to the right of the name.
4. **Located at** — the structure in game. The list contains every station and structure where
   your characters have assets (after a character sync), as "Name · id". If yours isn't there, put
   any item into it and sync characters, or type the id by hand. The field is used for labels,
   stock and automatic system detection.
5. **System** — "auto" by default: the system of the "Located at" structure (or the build system
   if it isn't known yet). The system determines the station's **cost index** and the **rig
   bonus**: rigs give more in low-sec and null-sec, and Forge accounts for that. To pick manually,
   use "Pick the station's system manually…"; the cross resets it to auto.
6. **Structure type** — find Raitaru, Azbel, Sotiyo, Athanor or Tatara. The structure's built-in
   bonuses (materials, time and cost for Engineering Complexes; reaction time for the Tatara) come
   from the SDE.
7. **Rigs and service modules** — find each rig by name (for example, `Standup M-Set Equipment
   Manufacturing Material Efficiency I`) and add it. Bonuses are computed from the SDE with
   stacking penalties.
8. **Station tax, %** — the structure owner's tax **in percent**: `1` = 1%, `0.5` = 0.5%.
9. **Which products go here** — product EVE groups and/or EVE categories. Empty — the station takes
   every product of its role that more specific stations didn't claim.
10. **Save**. After saving, a **"Computed bonus (from the saved fit): materials …%, time …%, job
    cost …%"** line appears under the rigs — that's how you know the rigs were recognized.

The "ME savings, %", "Time savings, %" and "Job cost savings, %" fields are used **only when neither
a structure type nor rigs are set** — for example, for someone else's structure where you only know
the resulting bonuses. As soon as a type or any rig is set, the manual percentages are ignored.

### The main rule: one card — one rig set

A card's rigs apply to **every** product routed to that card, while in the game each rig only
affects its own product types. So if one structure has rigs for different products, give it
**several cards** — one per rig (or per set of rigs for the same products) — and in each one list
the groups or categories the rig affects in "Which products go here". You can see what a rig
affects in game: Show Info on the rig.

Plus one card "for everything else" — no groups, no rigs, but with the structure type, so the
remaining products get at least the structure's built-in bonus.

### Examples

**A single Raitaru without rigs, everything built there.** One card: role "manufacturing
(everything else)", "Located at" — your Raitaru, structure type Raitaru, station tax, "Which
products go here" empty.

**A Sotiyo with an XL ship rig.**

| Card | Role | Type | Rigs | Which products go here |
|---|---|---|---|---|
| Sotiyo — ships | manufacturing | Sotiyo | Standup XL-Set Ship Manufacturing Efficiency I | category **Ship** |
| Sotiyo — everything else | manufacturing | Sotiyo | — | empty |

**A Raitaru for modules and T2 components.**

| Card | Role | Type | Rigs | Which products go here |
|---|---|---|---|---|
| Raitaru — modules | manufacturing | Raitaru | Standup M-Set Equipment Manufacturing Material Efficiency I, Standup M-Set Equipment Manufacturing Time Efficiency I | category **Module** |
| Raitaru — T2 components | T2 components (by group) | Raitaru | Standup M-Set Advanced Component Manufacturing Material Efficiency I | group **Construction Components** |
| Raitaru — everything else | manufacturing | Raitaru | — | empty |

**Reactions on an Athanor.** M-Set reactor rigs are split by reaction type, so — one card per
type:

| Card | Role | Type | Rigs | Which products go here |
|---|---|---|---|---|
| Athanor — composites | reactions | Athanor | Standup M-Set Composite Reactor Material Efficiency I | groups **Intermediate Materials**, **Composite** |
| Athanor — polymers | reactions | Athanor | Standup M-Set Hybrid Reactor Material Efficiency I | group **Hybrid Polymers** |
| Athanor — biochemical | reactions | Athanor | Standup M-Set Biochemical Reactor Material Efficiency I | group **Biochemical Material** |

**Reactions on a Tatara.** The L-Set rig `Standup L-Set Reactor Efficiency I` affects all
reactions, so one card is enough: role "reactions", type Tatara, this rig, "Which products go here"
empty.

**Invention and copying.** A card with the "invention" role (for example, a Raitaru with
`Standup M-Set Invention Cost Optimization I` and/or `Standup M-Set Invention Accelerator I`) and,
optionally, a "copying" card. Without them, invention and copying use the fallback multipliers and
the build system's cost index.

### A station in another system

A station doesn't have to be in the build system: set its "Located at" or pick the system by hand —
the cost index and rig bonuses will use its system. Forge does **not** model moving materials
between systems: materials are assumed to be delivered to the build system.

## 8. Stock structures and market structures

### How to find a structure id

- **Stock → Where things are**: hover over a station or structure — the tooltip shows
  "Name · id …".
- **Settings → Stations → Located at**: the list shows "Name · id".
- If the structure is in neither, your characters have no assets in it. Put any item there (even
  1 Tritanium) and click **Characters + structure markets**.

Forge learns a player structure's name and system via ESI, and only if one of your characters has
access to it (docking). Otherwise it's shown as "Structure <id>" and its system is unknown.

### Build location structures

**Settings → Logistics & market → Structures:**

- **Build location Engineering Complex** and **Build location Refinery** — the ids of your build
  structures. In "Auto" mode the stock counts what lies in them (and in the "Where to look for
  blueprints" locations) as already on hand.
- **Default market structure** — a market id; used only if the "Market structures" list below is
  empty.

### Market structures

**Settings → Logistics & market → Market structures** — where sales market prices come from.

1. Pick a structure in the **"— known structure —"** list and click **Add** — or type its id into
   the "structure id" field and click **By id**.
2. **Save**, then **Overview → Characters + structure markets**.
3. Check **Overview → Data sources → "Structure markets"**: the result for each structure.

Orders of all listed structures are merged into **one** sales market snapshot: lowest sell,
highest buy, total volume. Each structure is fetched by a character with access to its market:
first one who has assets there; on an access error the next one is tried. One structure failing
doesn't affect the others. A "⚠ not in hub system" note means the structure isn't in the sales
market's system: its orders still go into the snapshot — remove it if that's not what you want.

## 9. Taxes, market and freight

### Taxes

**Settings → Manufacturing → Manufacturing & taxes.** Values here are **fractions**, not percent:
`0.01` = 1%.

| Field | What to enter |
|---|---|
| SCC surcharge, fraction | SCC tax on job installation, `0.04` (4%) |
| Broker fee on sale, fraction | broker fee on your sales market |
| Sales tax, fraction | sales tax with your skills |
| Structure tax, fraction (fallback) | tax for jobs that have no station |
| Material / job cost / time multiplier (fallback) | `1.0` — no bonus; used only if the job's role has no station |
| Reprocessing efficiency | `0` — don't consider reprocessing; otherwise your rate, e.g. `0.85` |

Broker fee and sales tax default to `0`, which overstates profit. You can see the exact values in
game in the sell order window on your sales market.

### Where to buy and how to price sales

**Logistics & market → Market:**

- **Buy in <hub> (+ delivery)** and **Buy in <sales market>**. Both unchecked means the same as
  both checked. Forge takes the cheapest delivered option and accounts for how much is actually
  listed.
- **Sale: place a sell order** (at the lowest sell) or **Sale: straight into buy orders** (at the
  highest buy).

### Freight

**Logistics & market → Freight (logistics legs)** — three legs: hub → sales market, sales market →
build, build → sales market. A leg with zeros is free delivery (for example, when the hub and the
sales market are the same system).

Two modes:

- **per_m3** — pay per volume, like courier services. *ISK/m³* is the rate, *min. per order* is the
  minimum price of one order. The minimum is applied **once per order**, not per material. Example:
  800 ISK/m³, minimum 5,000,000 ISK.
- **by trips** (fixed_jump) — your own jump freighter or a fixed price per trip. *ISK per jump* —
  the cost of one one-way trip (fuel or carrier price), *capacity, m³* — the ship's cargo hold,
  *load factor 0–1* — how full you actually load it (for example, `0.9`). The first batch is one
  trip; each extra batch adds two (back empty, then loaded again): N batches = 2N − 1 trips. Volume
  is summed over the whole order.

**Ships that fly out themselves** — ship groups (jump freighters, carriers, dreadnoughts,
titans…) that fly to market under their own power. Their export is charged as the "ISK per jump"
of the "build → market" leg per ship, not by volume. The default list is already filled in.

## 10. Character roles and slot limits

The **Characters** tab:

- **Mfg.**, **Reactions**, **Science** — who may run which jobs in the schedule. "Science" means
  invention and copying.
  - No boxes checked for anyone — every character takes part in everything.
  - As soon as any box is checked, each pool uses **only the characters checked in it**. If you
    build reactions, check someone in the "Reactions" column: if it's empty while other columns are
    filled, nobody gets reaction jobs.
  - The exception is "Science": an empty column means science is done by those checked in "Mfg.".
  - A character with no boxes (while others have some) doesn't take part.
- **Forge limit mfg/react/sci** — how many of this character's slots the schedule may use. Empty —
  all slots from skills; `0` — the character is out of this pool. This lets you keep slots free for
  research and your own jobs. Jobs already running keep their slots until they finish.
- **Save roles**.

Forge derives slot counts from the character's skills. The "Updated" column shows when the
character was last synced; "login needed" — see [section 4](#where-access-is-stored).

## 11. Blueprints

**Settings → Blueprints:**

- **Where to look for blueprints** — the locations where your characters keep blueprints, with
  counts. Check the ones holding your working blueprints: only those (and blueprints in containers
  inside them) count as "your own". Nothing checked — all blueprints are yours. This affects ME/TE
  in calculations, blueprint cost, the "Own blueprints only" top list and which character the
  schedule assigns a job to. The list appears after a character sync.
- **Manual blueprint cost (ISK per run)** — for blueprints you don't own and can't invent (for
  example, purchased copies). For an invention T1 blueprint — the price of one T1 copy per attempt.

Forge reads **character blueprints**. It doesn't see corporation blueprints (in corporation
hangars). For those, set ME/TE in the Calculator basket (the ME field of the row) or the defaults:
"Settings → Planner → Default basket → ME if no own blueprint" (and TE).

## 12. Stock

The **Stock** tab decides what counts as already on hand and isn't bought again.

- **Auto** — the build structures from [section 8](#build-location-structures) plus the "Where to
  look for blueprints" locations. If the build structures aren't set, "Auto" finds almost nothing.
- **Pick manually** — checkboxes in the "Where things are" tree: a whole system (including future
  structures in it), a single station, structure or container. An unchecked box inside a checked
  system is an exception. **Start from the "Auto" selection** begins with what "Auto" picked.
- **Filters** — whose assets to count; don't count fitted modules, assembled ships, or items in
  cargo holds, drone bays, Asset Safety, etc. as stock. The tree greys out what is filtered away.
- **Exclusions and reserve** — what never to take from stock and how many units to leave alone.
- **Save stock**. The result is in the **Currently in stock** card.

Leftovers outside the build location are used too: the report brings them from the hub and the
sales market with the same freight and flags other systems.

## 13. Invention, always buy / build, planner

You can leave these at their defaults and come back later.

- **Settings → Manufacturing → Invention** — chance with the inventor's skills (the best of the
  "Science" role) and decryptor choice: "Auto — by cost", "No decryptor", "One for all", plus a
  decryptor for specific T2 products.
- **Always buy / Always build** — groups or items. A typical example: the **Fuel Block** group —
  always buy.
- **Settings → Planner** — whether to account for running jobs, whether to schedule T1 blueprint
  copying, who gets the job (anyone / prefer the blueprint owner / owner only), duration options for
  "Compare options", default basket values.
- **Settings → Recommendations** — weights and filters of the What to build tab, exclusions, top
  groups.

## 14. Auto-update

**Overview → Auto-update**: "Update in the background while Forge is open", intervals for the
market (30 minutes by default) and characters (60 minutes) → **Save**. A sync runs when more than
the interval has passed since the last successful one.

## 15. Check: your first calculation

1. **Calculator** → find an item you build → set the runs in the basket row → **Calculate
   basket**.
2. **Preparation / issues** — what's missing: blueprints, formula copies, market prices.
3. **By item → Job installation (main blueprint) — breakdown** — the "× system cost index
   <system>", "× station multiplier" and "+ facility tax + SCC" rows. Make sure the system is the
   one where the station is, and the station multiplier isn't `×1.000` (if the station has a cost
   bonus).
4. The **Materials** block (tree) — quantity, price in each hub, source (build / buy / stock).
   Clicking the source switches build ↔ buy.
5. Is there a sell price and profit? If not — [section 6](#6-locations-where-you-buy-build-and-sell).
6. **Schedule → Build schedule** — jobs laid out across your characters' slots.
7. **Generate build report** — opens the HTML report: shopping list, per-character instructions,
   schedule and cost tracking.

## 16. Updating, moving and backups

All your data lives in the program folder:

| File / folder | What it is |
|---|---|
| `forge.toml` | settings |
| `forge.db` | database: SDE, market, characters, stock |
| `reports/` | build reports and the actuals you entered in them |
| `.forge/` | window state, basket, icon cache |
| `forge_desktop.log` | error log |

- **New version:** unpack it into a new folder and move `forge.toml`, `forge.db` and `reports/`
  there. You don't need to add characters again.
- **Another computer:** copy the whole folder, then add your characters again: their access is
  stored in the Windows Credential Manager, not in the folder.
- **Backup:** `forge.toml`, `forge.db` and `reports/`. The most valuable are `forge.toml` (your
  settings) and `reports/` (builds and the actuals you entered); the data in `forge.db` can be
  downloaded again, but then you'll have to re-add characters and wait for the SDE and market
  history.

## 17. Troubleshooting

| Symptom | What to do |
|---|---|
| No sell price and no profit | The sales market is an NPC hub in another region, or a market structure isn't added / accessible. See [section 6](#6-locations-where-you-buy-build-and-sell) and [section 8](#market-structures). |
| "sso.client_id is not set in the config." | Install from source: enter your application's Client ID in "Settings → System". |
| "Timed out waiting for the SSO callback." | The login took more than 5 minutes, port 8765 is busy, or the application's Callback URL doesn't match "Callback port" in "Settings → System". |
| "Login needed again" / "login needed" | **Add character (EVE SSO)** with that character, then **Characters + structure markets**. |
| A structure is shown as "Structure <id>" | None of your characters has access (docking) to it, or characters haven't been synced yet. |
| Access error for a market structure | Your characters have no access to its market. Per-structure results — "Overview → Data sources". |
| No "Computed bonus" line on a station | Neither a structure type nor rigs are set, or the settings weren't saved. |
| A rig bonus applies to the wrong products | Split the rigs into cards and set groups or categories ([section 7](#the-main-rule-one-card--one-rig-set)). |
| Reactions or invention don't show up in the schedule | Roles are checked on the Characters tab, but the "Reactions" column (or "Science" together with "Mfg.") is empty ([section 10](#10-character-roles-and-slot-limits)). |
| "Auto" stock is empty | The build structures aren't set ([section 8](#build-location-structures)) — or switch to "Pick manually". |
| Forge doesn't see a blueprint | It's in a corporation hangar or outside the checked "Where to look for blueprints". |
| Reports don't open | All ports 8000–8010 are busy; the rest of the program works. |
| The window doesn't open | Check `forge_desktop.log` in the program folder. |

## 18. Editing forge.toml by hand

Everything in this guide is stored in `forge.toml` next to the program. Forge rewrites it on save
and doesn't keep comments. The reference with explanations of every key is
[forge.example.toml](../forge.example.toml).

Location keys are role identifiers, not system names:

| Role in Forge | Key in `forge.toml` |
|---|---|
| Purchasing hub | `[locations.jita]` |
| Sales market / 2nd hub | `[locations.c_j6mt]` |
| Build location | `[locations.gplb_c]` |
| Default market structure | `[structures] taj_mahgoon_market_id` |
| Build location Engineering Complex | `[structures] gplb_engineering_complex_id` |
| Build location Refinery | `[structures] gplb_refinery_id` |
| Market structures | `[structures] market_structures` |
| Stations | `[[facilities]]` |
| Freight | `[[freight_routes]]` (`from`/`to` are the location keys above) |

Top-level keys (character roles, "Where to look for blueprints", always buy / build) must come
**before** the first `[section]`, otherwise TOML assigns them to it.

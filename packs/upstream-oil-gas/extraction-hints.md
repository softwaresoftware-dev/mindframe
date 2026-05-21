# Extraction hints — upstream oil & gas pack

When `/mindframe:setup` bootstraps the vault and this pack is active, follow
these recipes to populate the pack's entity types from connected data
systems. Each recipe assumes the corresponding perception MCP (or direct
DB / REST access) is wired and validated.

For every entity below, write `<entity_directory>/<name-or-slug>.md` with
frontmatter and a stub body. Present every batch to the operator for
confirmation, edit, or drop before committing.

---

## From an Ignition Tag Historian

Tables of interest: `sqlth_drv`, `sqlth_scinfo`, `sqlth_te`, `sqlth_partitions`.

### Wells

Group `sqlth_te.tagpath` rows by the prefix up to (and not including) the
final tag-name segment. For example `FAY/PAD_NORTH_07/Smith_1H_25-7N-13W/Tubing_Pressure`
and `FAY/PAD_NORTH_07/Smith_1H_25-7N-13W/Wellhead_Temp` both belong to the
well `Smith_1H_25-7N-13W`. Write one `Wells/<well-name>.md` per group:

```yaml
type: well
name: <last-path-segment-of-prefix>
tag_prefix: <full-prefix>
tag_gateway: <sqlth_drv.name for the driver feeding these tags>
status: producing
```

### Operators

One per row in `sqlth_drv` — the gateway name typically maps to a system /
field / company name. Write `Operators/<gateway-name>.md`. The operator
should confirm the legal name in step 2.

### Cross-walks (sellerco_predecessor)

If two wells across different drivers share a stable identifier (api14 most
reliably; otherwise legal description; otherwise spud date proximity),
record the older one as `sellerco_predecessor` on the newer well's
frontmatter. Mark confidence in the well note body.

---

## From Quorum On Demand Land (REST)

Endpoints of interest:
- `GET /v1/leases`
- `GET /v1/leases/{name}/division-order`
- `GET /v1/wells/{well_id}/lease`

### Leases

One per row in `GET /v1/leases`:

```yaml
type: lease
name: <lease.name>
lease_number: <lease.lease_number>
legal_description: <lease.legal_description>
state: <lease.state>
county: <lease.county>
royalty_decimal: <lease.royalty_decimal>
primary_term_end: <lease.primary_term_end>
tsa_cutover_date: <lease.tsa_cutover_date>
operator: <lease.operator_of_record>
acquired_in: <lease.acquired_in>
```

The lease body should narrate the acquisition lineage, open variances, and
any open owner inquiries.

### Royalty owners

For each lease, fetch `GET /v1/leases/{name}/division-order` and write one
`Owners/<owner.name>.md` per non-working-interest owner. Working interest
holders of the operating company itself do not get a vault note (they're
the operator).

```yaml
type: royalty-owner
name: <owner.name>
owner_id: <owner.owner_id>
interest_type: <owner.type>
decimal_interest: <owner.decimal_interest>
lease: <lease.name>
```

---

## From Quorum FlowCal (REST)

Endpoints of interest:
- `GET /v1/meters`
- `GET /v1/wells/{well_id}/meter`
- `GET /v1/variances?meter_id=<id>&status=open`

### Meters

One per row in `GET /v1/meters`. The `wells_contributing` list FK populates
from `meter.wells_contributing[].well`. Allocation percentages live in the
body, not frontmatter.

### Open variances → annotations on the meter

For each open variance, append a short bullet to the meter note's "Open"
section with `[[<variance-id>]]` and the magnitude. Variance investigations
themselves are not core entities yet — they live as inline references.

---

## From a production-accounting system (ProCount / Enertia / OGsys)

Tables of interest: `wells`, `meters`, `monthly_closes`, `state_filings`,
`royalty_checks`.

### Cross-reference wells

PA's `wells` table is the canonical operator-of-record list. Join PA wells
to historian wells by `api14`. Update each `Wells/*.md` frontmatter with
PA-derived facts (`status`, `operator`, `first_prod`).

### Monthly closes → trailing context on each well

For each well, fetch the last 12 monthly closes. Don't write one note per
close; instead, write a "History" section in the well note that summarizes
the variance trend (mean %, worst month, worst %). The agent reads this for
"is this an outlier" framing.

### Open state filings

Draft state filings (status = `draft`) imply work the team owes the
regulator. Surface them on the affected well's note's "Active items"
section.

---

## From the operator (manual)

- The **acquisition** event slug, close date, basins, asset count, prior
  operator name, new operator name. Discoverable from data but typically
  faster to ask.
- Confirmation of cross-walks before they're committed.
- The plain-English name of each operator on the system.
- Whether each lease is currently producing, in P&A review, or in TSA window.

---

## CATALOG.md sections

After auto-discovery completes, regenerate CATALOG.md. For each active
entity type in this pack, add a section listing the most-queried fields
(see `mindframe/docs/kb-schema.md` for the format). Suggested per-entity
catalog rows:

- `well`: name, lease, pad, status, lift_type, tag_gateway
- `lease`: name, state/county, operator, primary_term_end, acquired_in
- `meter`: name, pipeline, allocation_method, calibration_due, open variances
- `royalty-owner`: name, lease, interest_type, decimal_interest
- `freeze-off`: slug, affected well, min_wellhead_temp_f, duration_min

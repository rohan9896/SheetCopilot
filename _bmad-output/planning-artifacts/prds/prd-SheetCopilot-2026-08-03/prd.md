---
title: SheetCopilot MVP Product Requirements Document
status: draft
created: 2026-08-03
updated: 2026-08-03
---

# PRD: SheetCopilot MVP

## 0. Document Purpose

This PRD defines the design-partner MVP for SheetCopilot. It is the product contract for product, UX, architecture, engineering, and quality work that follows. Requirements are grouped by capability and use stable identifiers. The document deliberately separates manufacturing intent, nominal part geometry, nesting, CAM-ready DXF output, and machine-specific execution so the product does not imply that an approved DXF is itself controller-ready G-code.

## 1. Vision

SheetCopilot is a web SaaS product for steel-fabrication workshops that converts customer engineering drawing PDFs into reviewed parametric part definitions and CAM-ready cutting files. It reduces the manual work of interpreting drawings, recreating flat parts in CAD, preparing quantities, nesting compatible parts onto stock sheets, and exporting DXFs for the workshop's existing CAM/CNC workflow.

The system uses AI to propose drawing interpretations, but it treats the proposal as a hypothesis rather than manufacturing truth. Each extracted value and feature retains confidence and source provenance. Manufacturing-critical unknowns, conflicts, and unsupported features are surfaced for focused human review; the system prefers `unknown` over guessing.

The MVP succeeds when a workshop engineer can move from a supported customer drawing to approved part geometry and an approved nested-sheet DXF with substantially less manual CAD effort, while preserving traceability to the source drawing and every human decision.

## 2. Target User and Journey

### 2.1 Primary User

The primary daily user is a workshop engineer who understands engineering drawings, flat-part manufacturing, stock constraints, and the workshop's CAM workflow. The workshop owner is the economic buyer and organization administrator. Permission-based roles determine who may approve and export; the pilot does not require review and approval by different people.

### 2.2 Jobs To Be Done

- Convert customer drawing PDFs into trustworthy cut-ready flat-part geometry without redrawing each part manually.
- Find ambiguous or missing manufacturing information before it reaches production.
- Preserve non-cutting requirements, such as countersinks, as explicit secondary operations.
- Combine compatible approved parts and quantities into material-efficient sheet layouts.
- Export files that import reliably into the workshop's existing CAM software.
- Trace every exported sheet back to the customer drawing, approved part revision, nesting inputs, and approving user.

### 2.3 Key User Journey

**UJ-1. A workshop engineer prepares a customer cutting job with minimal manual CAD work.**

An organization receives one or more customer drawing PDFs and required quantities. A signed-in workshop engineer creates a job and uploads each drawing. SheetCopilot preserves the originals, interprets the drawings, and produces reviewable Part Definition proposals. The engineer focuses on flagged items, compares each proposal with highlighted source regions, corrects or adds supported geometry, and approves a validated part revision. The engineer then selects compatible approved parts and quantities, chooses a saved stock preset, adjusts process constraints, and runs nesting. After reviewing or editing placements, the engineer approves the Nesting Job and downloads one nominal-geometry DXF per stock sheet plus a Manufacturing Manifest. The DXFs import into the design partner's nominated CAM application, while any secondary operations remain visible for downstream work. Value is delivered when production preparation takes materially less time than the current interpretation-CAD-nesting-export process without hiding manufacturing-critical uncertainty.

**Edge cases:** unreadable or unsupported drawings are rejected with a reason; missing critical dimensions block approval; incompatible materials or thicknesses cannot share a Nesting Job; secondary operations require acknowledgment before cut-profile export.

### 2.4 Non-Users for MVP

- Teams requiring automatic 3D solid-model reconstruction.
- Shops expecting machine-controller-specific NC programs without a CAM step.
- Users processing assemblies, bent sheet-metal parts, weldments, or complex machined components.
- Organizations requiring integrated ERP, quoting, or live stock/remnant inventory in the pilot.

## 3. Glossary

- **Source Drawing** — The original customer-supplied, single-page PDF preserved by SheetCopilot.
- **Part Definition** — The versioned, canonical JSON representation of one flat part and its manufacturing annotations.
- **Feature** — A supported geometric or manufacturing item in a Part Definition, such as an edge, arc, hole, cut-out, dimension, tolerance, or secondary operation.
- **Source Anchor** — A page region and source text linking an extracted item to evidence in the Source Drawing.
- **Blocking Issue** — An unresolved condition that could change units, material, thickness, quantity, cut geometry, placement, or manufacturability and therefore prevents approval.
- **Part Revision** — An immutable approved or draft version of a Part Definition.
- **Manufacturing Plan** — The separation of 2D cutting geometry, secondary operations, warnings, and acknowledgments for a Part Revision.
- **Secondary Operation** — A required operation not completed by the exported 2D cutting profile, such as countersinking.
- **Stock Preset** — A reusable combination of material, thickness, sheet dimensions, and default nesting constraints.
- **Nesting Job** — Approved Part Revisions, quantities, compatible Stock Presets, constraints, and placements prepared for sheet export.
- **CAM-ready DXF** — Validated nominal geometry intended for import into CAM software. It is not machine-specific G-code.
- **Manufacturing Manifest** — The export record listing sheets, parts, revisions, quantities, warnings, Secondary Operations, acknowledgments, and approvals.

## 4. Product Requirements

### 4.1 Organization Access and Job Intake

**Description:** SheetCopilot keeps customer drawings and manufacturing outputs private to an organization and gives the workshop a traceable container for each customer job. Realizes UJ-1.

- **FR-1: Organization isolation.** A signed-in user can access only drawings, Part Revisions, Nesting Jobs, and exports belonging to organizations in which the user has an active membership.
- **FR-2: Permission-based actions.** Organization roles control upload, edit, approve, and export permissions. One authorized user may review and approve during the pilot.
- **FR-3: Customer job.** An authorized user can create a job with customer reference, drawing files, required quantities, and optional due-date or notes.
- **FR-4: Original preservation.** The system preserves every Source Drawing unchanged and links all derived artifacts to it.

### 4.2 Upload and Processing

**Description:** The system accepts supported vector PDFs and clean scans, reports progress, and fails transparently.

- **FR-5: Supported upload.** The system accepts a single-page PDF containing one primary flat part and enforces configured file-size and page limits.
- **FR-6: Input screening.** The system identifies encrypted, corrupt, unreadable, multi-page, multi-primary-part, or clearly unsupported inputs before manufacturing approval.
- **FR-7: Progress visibility.** The user sees durable processing stages for upload, document analysis, geometry construction, and validation.
- **FR-8: Understandable failure.** Failed processing shows a human-readable reason, affected stage, and whether retry may help.
- **FR-9: Safe retry.** An authorized user can retry a retryable job without losing the Source Drawing, review history, or creating duplicate approved revisions.

### 4.3 Drawing Interpretation and Part Definition

**Description:** AI-assisted interpretation produces a structured proposal with evidence. Deterministic geometry construction and validation remain authoritative.

- **FR-10: Drawing metadata extraction.** The system extracts part name and number, units, material, thickness, and title-block information when available.
- **FR-11: Supported geometry extraction.** The system extracts straight lines, circular arcs, full circles, circular holes, and simple internal cut-outs for the primary flat part.
- **FR-12: Manufacturing annotation extraction.** The system extracts linear dimensions, radii, diameters, angles, tolerances, repeated-feature counts, and relevant section-view details.
- **FR-13: Reference-geometry exclusion.** The system distinguishes the primary part from dimensions, construction lines, centerlines, hidden/reference geometry, adjacent parts, and title-block graphics.
- **FR-14: Per-item evidence.** Every extracted item includes a Source Anchor, confidence status of `high`, `medium`, `low`, or `unknown`, and extraction provenance.
- **FR-15: No invented values.** Missing or conflicting manufacturing-critical values remain `unknown` and create Blocking Issues.
- **FR-16: Canonical model.** Processing produces a versioned Part Definition that is the common source for preview, validation, Manufacturing Plan, DXF generation, and nesting.
- **FR-17: Downloadable JSON.** An authorized user can download the approved Part Definition JSON for traceability and downstream integration.

### 4.4 Review, Correction, Validation, and Revision

**Description:** Review is exception-focused but provides complete evidence and guided editing when the interpretation is wrong.

- **FR-18: Synchronized review.** The user can view the Source Drawing and proposed geometry together, select a Feature, and see its Source Anchor and extracted value.
- **FR-19: Guided correction.** The user can confirm, correct, reject, reposition, or add supported lines, arcs, circles, holes, dimensions, and simple cut-outs without a full CAD sketcher.
- **FR-20: Review provenance.** Each Feature records whether it was AI-proposed, confirmed, corrected, rejected, or user-added, together with acting user and timestamp.
- **FR-21: Geometry validation.** The system validates that the outer contour is closed and non-self-intersecting and that internal cut geometry is closed, valid, and inside the outer contour.
- **FR-22: Manufacturing validation.** The system validates confirmed units, material, thickness, dimensions, feature positioning, and resolution of all Blocking Issues.
- **FR-23: Explicit approval.** Only an authorized user can approve a Part Revision, and approval is blocked until all Blocking Issues are resolved.
- **FR-24: Immutable revision.** Any change after approval creates a new Part Revision; existing artifacts remain linked to the previously approved revision until intentionally regenerated.

### 4.5 Manufacturing Plan and Part Export

**Description:** The system preserves the difference between what a 2D cutter produces and what the finished part still requires.

- **FR-25: Operation classification.** The Manufacturing Plan separates cuttable outer/internal geometry from Secondary Operations and unsupported operations.
- **FR-26: Secondary-operation acknowledgment.** A cuttable Part Revision containing a Secondary Operation may be exported only after an authorized user acknowledges that cutting does not complete the part.
- **FR-27: Part DXF.** An approved Part Revision can produce a nominal-geometry DXF containing one valid closed outer profile and supported internal through-cuts.
- **FR-28: Part preview.** Each generated Part DXF has a visual preview derived from the exported geometry.
- **FR-29: Traceable part export.** The Part DXF and Manufacturing Plan record the Source Drawing, Part Revision, units, approving user, approval date, and export date.

### 4.6 Stock Presets and Nesting

**Description:** The workshop combines compatible approved parts and quantities into validated, editable sheet layouts.

- **FR-30: Saved Stock Presets.** An authorized user can save material, thickness, sheet dimensions, and default process constraints for reuse.
- **FR-31: Compatible inputs.** A Nesting Job can contain multiple approved Part Revisions and quantities only when material and thickness match the selected Stock Preset.
- **FR-32: Job-level constraints.** The user can set sheet count, kerf allowance used for spacing, part-to-part clearance, edge margin, allowed rotations, and grain-direction restrictions.
- **FR-33: Safe orientation.** Mirroring is disabled by default and cannot occur without an explicit future capability.
- **FR-34: Optimization objective.** The optimizer minimizes required sheet count first and unused area second while satisfying all placement constraints and requested quantities.
- **FR-35: Multi-sheet result.** The optimizer can place quantities across multiple sheets and clearly reports any quantity that could not be placed.
- **FR-36: Editable placements.** The user can move, rotate, remove, restore, or re-optimize placements while the system continuously enforces boundaries, clearances, compatibility, and collision rules.
- **FR-37: Nest validation.** A Nesting Job cannot be approved while any placement overlaps, crosses a sheet boundary or edge margin, violates orientation constraints, or leaves required quantity unplaced.
- **FR-38: Nesting approval and revision.** An authorized user explicitly approves a validated Nesting Job. Changes to quantities, stock, constraints, or placements create a new Nesting Job revision and invalidate its prior approval.

### 4.7 CAM-ready Export and Audit

**Description:** Approved nesting results become a traceable package for the workshop's CAM workflow.

- **FR-39: Per-sheet DXF.** An approved Nesting Job exports one nominal-geometry DXF for each used stock sheet.
- **FR-40: Sheet preview.** Every exported sheet includes a visual preview showing sheet boundary, part identity, revision, quantity, and placement.
- **FR-41: Manufacturing Manifest.** The export package includes part and sheet identities, quantities, material, thickness, stock dimensions, utilization, Secondary Operations, warnings, acknowledgments, Part Revisions, Nesting Job revision, approving user, and timestamps.
- **FR-42: Export traceability.** Each exported placement is traceable to exactly one approved Part Revision and Source Drawing.
- **FR-43: Independent validity.** Generated DXFs must reopen in an independent parser with the same entity count, units, and closed geometry represented by the approved canonical models.
- **FR-44: Pilot CAM compatibility.** The design-partner pilot is not accepted until the nested DXF imports successfully and is visually verified in one CAM application nominated by the partner.

## 5. State Models

### 5.1 Drawing and Part Revision

```text
Uploaded
→ Processing
→ Needs Review
→ Ready for Approval
→ Approved
→ Part DXF Generated
```

Failure states: `Unsupported`, `Processing Failed`, and `Validation Failed`.

### 5.2 Nesting Job

```text
Draft
→ Optimizing
→ Needs Review
→ Ready for Approval
→ Approved
→ Exported
```

Failure states: `Invalid Inputs`, `Optimization Failed`, and `Validation Failed`.

## 6. Product Data Contracts

### 6.1 Part Definition v1

The downloadable canonical JSON must contain, at minimum:

```json
{
  "schemaVersion": "1.0",
  "units": "mm",
  "part": {
    "name": "string-or-null",
    "number": "string-or-null",
    "material": "string-or-unknown",
    "thickness": "number-or-unknown"
  },
  "coordinateSystem": {
    "origin": [0, 0],
    "axes": "right-handed-2d"
  },
  "geometry": {
    "outerContour": [],
    "internalFeatures": []
  },
  "dimensions": [],
  "tolerances": [],
  "secondaryOperations": [],
  "sourceAnchors": [],
  "blockingIssues": [],
  "validation": {},
  "revision": {},
  "approval": {}
}
```

Every geometry primitive and manufacturing item must have a stable identifier, defined coordinates or relationships, Source Anchor, confidence, review state, and validation state. Supported v1 primitives are line segments, circular arcs, and circles. The formal JSON Schema and primitive field shapes belong in the architecture artifact while preserving this product-level contract.

### 6.2 Nesting Job Contract

A Nesting Job must identify approved Part Revision references and quantities, Stock Preset and sheet count, process constraints, optimization objective, sheet placements, unplaced quantities, material utilization, validation results, revision, and approval.

### 6.3 Export Package Contract

An Export Package contains immutable references to the approved inputs, one DXF and preview per sheet, and one Manufacturing Manifest. DXFs preserve nominal confirmed units and geometry. Machine-specific kerf compensation, lead-ins, cut ordering, tool selection, and postprocessing remain responsibilities of downstream CAM.

## 7. Non-Functional Requirements and Guardrails

- **NFR-1: Manufacturing safety.** No known unresolved Blocking Issue may exist in an approved Part Revision or Nesting Job.
- **NFR-2: Deterministic output.** The same approved Part Revision and export configuration must produce geometrically equivalent output regardless of the vision model used during extraction.
- **NFR-3: Organization privacy.** Files and derived data must be encrypted in transit and at rest and isolated by organization authorization checks.
- **NFR-4: Auditability.** Approvals, corrections, acknowledgments, status changes, retries, revisions, and exports must retain actor and timestamp provenance.
- **NFR-5: Failure recovery.** Processing and optimization jobs must be retryable without silent data loss or duplicate approvals.
- **NFR-6: Explainability.** A user must be able to trace any extracted or flagged item back to visible evidence in the Source Drawing.
- **NFR-7: Provider portability.** The extraction pipeline must permit evaluated model-provider replacement without changing the Part Definition contract.
- **NFR-8: Pilot performance.** [ASSUMPTION] Median time from supported upload to first reviewable Part Definition should be no more than five minutes under agreed pilot file limits.
- **NFR-9: Accessibility and usability.** Blocking states, confidence, validation results, and approvals must not rely on color alone; the review workspace must remain usable at common desktop resolutions.
- **NFR-10: Observability.** Every processing and optimization stage must emit duration, outcome, retry, provider/model version, and validation metrics without exposing customer drawing content in logs.

## 8. Explicit Non-Goals for MVP

- Machine-controller-specific G-code, NC programs, or postprocessors.
- CAM toolpaths, lead-ins, pierce strategy, cut sequencing, or machine control.
- Fusion models, CadQuery output, STEP files, or arbitrary 3D models.
- Multiple unrelated primary parts extracted from one drawing page.
- Assemblies, bent sheet-metal parts, threads, weldments, and complex 3D machining features.
- Automatic completion of parts with missing critical dimensions.
- Live stock, remnant, reservation, or consumption inventory.
- ERP, quotation, material costing, cut-time estimation, and machine integrations.
- Automatic export without explicit human approval.

## 9. MVP Acceptance Scenarios

1. **Golden drawing interpretation.** For `docs/sample-drawings/413-013-000600-00-001-IDM-030678118_EN_260802_200426.pdf`, the system identifies the primary curved wear-plate profile, metric units, part number `EZ 413-13-600`, material `S235J2G3`, thickness `25`, the three primary hole locations, radial/angular geometry, and the relevant section-view operation while excluding adjacent/reference geometry.
2. **Secondary-operation preservation.** The sample's through-hole/countersink detail is represented as cuttable through geometry plus a Secondary Operation; export requires acknowledgment and the Manufacturing Manifest preserves the operation.
3. **Critical ambiguity.** A missing contour dimension or unconfirmed scale creates a Blocking Issue and prevents Part Revision approval and downstream nesting.
4. **Guided correction.** A workshop engineer corrects an uncertain dimension, revalidates the geometry, and approves a new immutable Part Revision with source and user provenance.
5. **Mixed-part nesting.** The engineer selects multiple compatible approved Part Revisions and quantities, uses a saved Stock Preset, receives a collision-free multi-sheet result, edits placements, and approves it.
6. **Compatibility rejection.** Parts with different material or thickness cannot enter the same Nesting Job and receive a clear corrective message.
7. **DXF integrity.** Every exported sheet reopens in an independent parser, matches approved placement geometry, and imports successfully into the pilot partner's nominated CAM application.
8. **Traceability.** A downloaded sheet and each placement trace back to the Source Drawing, Part Revision, Nesting Job revision, approval, and Manufacturing Manifest.
9. **Organization isolation.** A user cannot discover or access another organization's Source Drawings or derived artifacts.
10. **Retry behavior.** A retryable provider or optimization failure can be retried without losing review work or creating duplicate approved artifacts.

## 10. Success Metrics

### Primary Metrics

- **SM-1: Production-preparation time.** [ASSUMPTION] Reduce median time from drawing receipt to approved nested DXF by at least 50% against the design partner's measured manual baseline.
- **SM-2: Supported conversion rate.** [ASSUMPTION] At least 70% of screened in-scope pilot drawings reach a reviewable Part Definition without manual CAD reconstruction from scratch.
- **SM-3: Safety gate effectiveness.** Zero approved test-corpus exports contain a known unresolved manufacturing-critical issue.
- **SM-4: Nesting quality.** Valid SheetCopilot nests use no more sheets than the workshop's baseline nest for the same approved parts, quantities, stock, and constraints.

### Secondary Metrics

- Time from upload to first reviewable Part Definition.
- Time from review start to Part Revision approval.
- Time from nesting input to approved sheet export.
- Number and severity of user corrections per drawing.
- Percentage of drawings blocked by missing information or unsupported features.
- Material utilization and estimated scrap area per sheet.
- Repeat usage by the same organization.
- Estimated manual CAD hours saved.

### Counter-Metrics

- **SM-C1: Hidden ambiguity.** Do not reduce review time by suppressing low-confidence or manufacturing-critical issues.
- **SM-C2: Forced conversion.** Do not improve conversion rate by guessing missing dimensions or relabeling unsupported drawings as supported.
- **SM-C3: Nominal utilization.** Do not improve utilization by violating clearances, grain restrictions, quantity, or placement validity.

## 11. Risks and Mitigations

| Risk | Product mitigation |
|---|---|
| Vision extraction appears confident but is wrong | Per-feature evidence, calibrated confidence, deterministic validation, Blocking Issues, explicit approval, and corpus evaluation |
| Reference or adjacent geometry becomes part geometry | Primary-part scoping, Source Anchors, overlay review, and golden tests using the sample drawing |
| A 2D export hides unfinished machining | Manufacturing Plan, Secondary Operation classification, acknowledgment, and Manufacturing Manifest |
| Nesting result is mathematically valid but impractical | Editable placements, process constraints, preview, and explicit Nesting Job approval |
| DXF does not import into the shop workflow | Independent parser validation plus nominated pilot CAM import acceptance |
| Provider capability, price, or availability changes | Provider-neutral Part Definition contract and evaluation-based model selection |

## 12. Open Questions

1. Which CAM application and DXF dialect/version will the design partner nominate for pilot acceptance?
2. What upload file-size, scan-resolution, processing-latency, and retention limits should apply to the pilot?
3. Which drawing languages beyond English are required for the initial corpus?
4. What measured manual preparation-time and material-utilization baselines will be used to confirm the draft success targets?
5. Which exact role names and permission matrix should the organization administration experience expose?

## 13. Assumptions Index

- The MVP is a web SaaS design-partner pilot.
- The primary operator is a workshop engineer; the workshop owner is buyer and administrator.
- Supported inputs are single-page vector PDFs and clean scans with one primary flat part.
- Vision-model providers are replaceable and selected through evaluation rather than hard-coded.
- Part Definition JSON and DXF are committed outputs; Fusion and CadQuery are deferred.
- Nesting supports mixed compatible Part Revisions, saved Stock Presets, and job-level edits without live inventory.
- CAM-ready means nominal DXF geometry for downstream CAM, not machine-specific G-code.
- Authorized users may both review and approve during the pilot.
- The initial performance and adoption targets in NFR-8, SM-1, and SM-2 require confirmation against pilot baselines.

# Gridlock 2.0 — Demo Script & Talking Points

A 3–4 minute walkthrough. The numbers below come from the full dataset; the live
figures are in the banner at the top of the map (`summary.json`).

---

## The one-line pitch
> "Police parking data tells you where police *went*, not where illegal parking
> *is*. Gridlock corrects that bias, predicts the true hotspots, scores their
> traffic impact, and tells officers where enforcement isn't reaching."

---

## The 4 numbers to quote (top banner)
1. **298,445 violations** analyzed (Nov 2023 – Apr 2024).
2. **~20 enforcement blind spots** — high predicted impact, low patrol.
3. **2,352 recidivist vehicles** (one scooter: 55 tickets).
4. **Under 1% of tickets written 3 PM–10 PM** (peak hour is 10–11 AM) — the
   evening, when commercial parking demand peaks, is an enforcement dead zone.

---

## Walkthrough (what to click, what to say)

**1. Open on the heatmap.**
> "This is the raw data — 298k violations. Looks like a citywide map of illegal
> parking. It isn't. It's a map of where patrols drove."

**2. Hit the `Raw counts` toggle (top banner).**
> "If we naively rank hotspots by ticket count — what most solutions do — we get
> the busy central junctions: City Market, Shivajinagar. These are just the
> heavily-patrolled beats. We've rediscovered the patrol roster."

**3. Switch to `Debiased`.**
> "Now we correct for patrol coverage — inverse-probability weighting, plus we
> anchor on the 50% of records that are *camera*-detected and therefore unbiased.
> The map changes: new cells light up that the raw counts buried."
- Point out the **overlap stat**: the debiased and naive top-150 lists share only
  **~6% of cells** — *concrete proof the model isn't just echoing patrol routes.*
  (IPW deliberately discounts the most heavily-patrolled, highest-count cells, so
  the debiased priorities are almost entirely different locations.)

**4. Turn on `Highlight blind spots` (magenta rings).**
> "These ~20 are the money slide: the model predicts high illegal-parking
> pressure AND they sit on high-capacity roads — so a blockage chokes real
> traffic — BUT observed patrol there is low. High impact, low enforcement.
> That's where to send the next patrol."

**5. Click a blind-spot circle.**
> "Each one is quantified: predicted latent rate, congestion impact from road
> geometry, the ward it's in. Actionable, not just a heatmap."

**6. Turn on `Recidivist vehicles` (blue dots).**
> "Separate lever: 2,352 vehicles offend repeatedly, often at the *same* spot —
> delivery fleets, auto stands. That's a targeted-enforcement problem, not a
> spatial one. 193 show a fixed-location pattern."

**7. Ward panel (left).**
> "Everything rolls up to BBMP wards so a station commander sees their own
> priorities, ranked."

---

## Hard questions, crisp answers

**"How is this different from just counting violations?"**
> "Counting violations ranks where police already are. We estimate the *latent*
> rate — what you'd see if enforcement were uniform — using three corrections:
> inverse-probability weighting by patrol intensity, a camera-detected anchor set
> that's bias-free, and device-ID negative sampling that recovers
> patrolled-but-clean cells. The debiased map shares only ~6% of its top cells
> with the naive one."

**"Isn't predicting on under-sampled areas just guessing?"**
> "That's why we validate with spatio-temporal cross-validation — train on early
> months, hold out later ones, AND hold out a whole geographic quadrant. We
> explicitly test generalization to unseen areas, not random rows."

**"Where's the congestion impact coming from — do you have traffic data?"**
> "Road geometry from OpenStreetMap — class, lane count, capacity. A violation on
> a 4-lane primary blocks far more flow than one on a residential lane. No paid
> traffic API needed; it runs free. (We can overlay Mappls live traffic too.)"

**"What would you add with more time?"**
> "Three things, all scoped in the README: a VIIRS night-light layer for an
> equity analysis (is enforcement fair across income levels?), an STGCN to model
> congestion spillover between cells, and a set-cover optimizer that turns the
> hotspot map into a one-click patrol route."

---

## If the map loads blank
The Mappls token expired (~24h life). Regenerate in the Mappls console and paste
it into `frontend/src/App.js` (`MAPPLS_TOKEN`).

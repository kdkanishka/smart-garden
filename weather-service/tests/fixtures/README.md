# snow1g fixture

`snow1g.json` is a synthetic, reduced representative payload using the fields
documented in implementation-plan.md Section 3.3, rather than a live API capture.
The timestamps and amounts are chosen for deterministic boundary calculations.
Amounts represent kg/m² (mm water equivalent) for each explicit interval.
The documented missing-value sentinel is 9999; tests inject it into this fixture.
Unrelated upstream fields are omitted. Tests never access the network.

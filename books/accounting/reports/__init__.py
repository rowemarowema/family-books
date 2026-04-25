"""Reports module — engine-side computations that views/exports render.

The split is deliberate: this package produces immutable, ORM-derived
data structures (dataclasses); rendering is downstream in books.web.
A trial-balance HTML page, a CSV export, and a PDF export ALL call the
same compute_trial_balance() and disagree on nothing but presentation.

That separation is what makes the cell-level tie-out tests possible:
parse the rendered output, compare every cell to the engine's
TrialBalanceRow field. If they ever drift, one of the two changed and
needs to come back into alignment.
"""

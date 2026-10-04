"""Evaluation system: the only consumer allowed to join MatchEyes output with hidden ground truth.

It may import `matcheyes` (to run the engine on observable data) and `matcheyes_synth` (to read
the answer key). Neither of those may import this package. Excluded from the deployable wheel.
"""
